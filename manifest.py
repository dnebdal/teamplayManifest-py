#!/usr/bin/env python3

# SPDX-License-Identifier: MIT
#
# Copyright (c) 2024 Daniel J. H. Nebdal
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
from datetime import datetime, timezone
import json
import zipfile
import os.path
import re

"""Functions to parse and generate Teamplay manifests, and a CLI tool.

As described at https://github.com/dnebdal/teamplayManifest-common , the Siemens Teamplay system
needs a way to store metadata next to data files. The Manifest class and helper functions in this file can 
generate and read those Manifest files, update them to mark a job as done, and automatically package a zip file 
containing a manifest and the files it refers to, using a standardized filename scheme.

Used as a command line tool, it takes a verb and a manifest or zip file, and can show various information, extract just
the manifest from a zip file, and given a manifest it can package it and the files mentioned in it to a zip file
with a name following a standard scheme.

Typical usage:

from manifest import Manifest, package_manifest
infiles = [
  {'Filename':'methylation_0001.csv', 'Description':'Methylation', 'MIME':'text/csv'},
  {'Filename':'vcf_0001.vcf', 'Description':'Mutation', 'MIME':'text/tab-separated-values'}
]
# These files are from "Patient-0001", taken at "End of Treatment", and are meant to be analysed 
# in the Teamplay container called "OUS0001".  
man = Manifest.new(patientID="Patient-0001", encounter="End of Treatment", performer="OUS0001", files=infiles)

# This creates a zip file, assuming the csv and vcf files are in the current working directory
package_manifest(man)
"""

def try_nested_key(d, key_list, fallback=""):
    """Recursively look up keys in a nested dictionary; returns a fallback if it fails."""
    for k in key_list:
        try:
            d = d[k]
        except (KeyError, TypeError):
            return fallback
    return d


def clean_for_filename(s):
    """Replace anything not ASCII alphanumerics or -_() with underscores"""
    s = s.encode("ASCII", "replace").decode("ASCII")
    s = s.replace('?', '_')
    s = re.sub("[^-_()a-zA-Z0-9]", "_", s)
    return s


class FileAttachmentList:
    """A list of files with some metadata.

    Each file is a dictionary with three keys:
    - Filename: A bare filename, no path or protocol.
    - Description: What _is_ this file? Often an omic, like "Methylation".
    - MIME: The MIME type, e.g. "text/plain".

    This will ultimately be stored as an array of HL7 FHIR Attachment elements,
    see  https://www.hl7.org/fhir/datatypes-definitions.html#Attachment .
    """
    def __init__(self, files=None):
        """Return a FileAttachmentList, optionally pre-filled with one or more files."""
        super().__init__()
        self._files = []
        if files is not None:
            self.insert(files)

    def insert(self, f):
        """Insert one or more files. Accepts one dict, or an iterable containing multiple."""
        if not ('keys' in dir(f)):
            for i in f:
                self.insert(i)
            return

        clean_f = {
            'Filename': f['Filename'],
            'Description': f['Description'],
            'MIME': f['MIME']
        }
        self._files.append(clean_f)

    def __len__(self):
        """Return the number of files in the list"""
        return len(self._files)

    def __repr__(self):
        return "FileAttachmentList(" + repr(self._files) + ")"

    @property
    def files(self):
        """Just the file names"""
        return [f['Filename'] for f in self._files]

    @property
    def table(self):
        """An array of dicts, one per file"""
        return self._files

    @property
    def HL7_table(self):
        """Returns a carefully constructed dict representation that will serialize to valid HL7 FHIR JSON."""
        return [
            dict(
                type=dict(text=f["Description"]),
                valueAttachment=dict(
                    contentType=f["MIME"],
                    url="file://" + f["Filename"])
            )
            for f in self._files]

    def __str__(self):
        """Return a human-readable multiline presentation of the files in this list."""
        text = ["\n  ".join((
            "-> " + " " + f["Filename"],
            "   MIME: " + f["MIME"],
            "   Description: " + f["Description"]
        )) for f in self._files]
        return "\n".join(text)


class Manifest(dict):
    """A representation of a Teamplay manifest - a HL7 FHIR Task in JSON format.

    A container for metadata about a job to be performed on Siemens Teamplay.
    See https://github.com/dnebdal/teamplayManifest-common for more details.

    Manifest objects can be constructed in a couple of ways:
    - New, by providing all required information to .new()
    - From an existing JSON file, with .from_file()
    - From JSON text in a string, with .from_json()
    - From a dictionary in the style the json module produces, with .from_parsed().

    Given a manifest describing a job to be done (.state == "request") and a list of generated files,
    it can be converted to a manifest describing a finished job with .mark_done() .

    To get a JSON representation, use the .json property - or the package_manifest() helper method,
    which lives outside the class.
    """
    status = "requested"
    authoredOn = ""
    lastModified = ""
    patientID = ""
    zipfile = ""
    encounter = ""
    performer = ""
    ts = ""
    inputFiles = FileAttachmentList()
    outputFiles = FileAttachmentList()

    def __init__(self):
        super().__init__()
        self.authoredOn = Manifest.__HL7_timestamp__()

    def __fill_from_parsed(self, parsed: dict):
        """Return a manifest filled in from a dict, using the keys and nesting of the JSON format."""
        self.status = parsed["status"]
        self.authoredOn = parsed["authoredOn"]
        self.zipfile = try_nested_key(parsed, ("for", "reference"))
        self.patientID = try_nested_key(parsed, ("focus", "reference"))
        self.encounter = try_nested_key(parsed, ("encounter", "reference"))
        self.performer = try_nested_key(parsed, ("requestedPerformer", 0, "reference", "reference"))
        self.inputFiles = FileAttachmentList([
            dict(Filename=a["valueAttachment"]["url"].removeprefix("file://"),
                 MIME=a["valueAttachment"]["contentType"],
                 Description=a["type"]["text"])
            for a in parsed["input"]])
        if self.status == "completed":
            self.outputFiles = FileAttachmentList([
                dict(Filename=a["valueAttachment"]["url"].removeprefix("file://"),
                     MIME=a["valueAttachment"]["contentType"],
                     Description=a["type"]["text"])
                for a in parsed["output"]])
        return self

    def __str__(self):
        """Return a human-readable string representation, including the input and output files (if any)."""
        input_files_str = str(self.inputFiles)
        output_files_str = str(self.outputFiles)
        if self.status == "completed":
            res = '\n'.join([
                f"Manifest for [{self.patientID}] @ [{self.encounter}] on [{self.performer}]",
                f"Status   {self.status}",
                f"Created  {self.authoredOn}",
                f"Finished {self.lastModified}",
                f"[ Input ]\n{input_files_str}",
                f"[ Output ]\n{output_files_str}"
            ])
        else:
            res = '\n'.join([
                f"Manifest for [{self.patientID}] @ [{self.encounter}] on [{self.performer}]",
                f"Status   {self.status}",
                f"Created  {self.authoredOn}",
                f"[ Input ]\n{input_files_str}",
            ])

        return res

    def __iter__(self):
        """Returns the key/value pairs."""
        attrs = ["status", "authoredOn", "zipfile", "patientID", "encounter",
                 "performer", "inputFiles", "outputFiles"]
        return ((attr, self.__getattribute__(attr)) for attr in attrs)

    @classmethod
    def from_file(cls, filename):
        """Construct a Manifest object from the contents of a Manifest JSON file."""
        instance = cls()
        if 'read' in dir(filename):
            parsed = json.load(filename)
        else:
            parsed = json.load(open(filename, "r"))
        return instance.__fill_from_parsed(parsed)

    @classmethod
    def from_json(cls, json_text):
        """Construct a Manifest object from a JSON string."""
        instance = cls()
        parsed = json.loads(json_text)
        return instance.__fill_from_parsed(parsed)

    # noinspection PyPep8Naming
    @classmethod
    def new(cls, patientID, encounter, performer, files):
        """Construct a Manifest object from the required fields and a list of files.

        - patientID: The name of the patient or sample to be analyzed.
        - encounter: The timepoint/event the data is from.
        - performer: The ID of the Teamplay analysis container to run on this data.
        - files: A list of dictionaries describing the data files. Each dict must contain:
            - Filename : A bare filename, no path or protocol.
            - Description: A description of the file - "Methylation" or "CT scan slice"
            - MIME: MIME type of the file, e.g. text/plain or image/tiff (see the web page or README)
        """
        self = cls()
        self.patientID = patientID
        self.encounter = encounter
        self.performer = performer
        self.inputFiles = FileAttachmentList(files)
        return self

    def mark_done(self, out_files):
        """Mark the task completed, set lastModified to now, and insert the given output files (see .new() )."""
        self.status = "completed"
        self.lastModified = Manifest.__HL7_timestamp__()
        self.outputFiles = FileAttachmentList(out_files)
        return self

    @property
    def __div_text__(self):
        """Returns a HTML div describing the manifest. For internal use."""
        res = "<div xmlns='http://www.w3.org/1999/xhtml'>"
        res += "Output" if self.status == "completed" else "Input"
        res += f" task for {self.patientID}, created {self.authoredOn}"
        res += "</div>"
        return res

    def __HL7_dict__(self):
        """Return a dictionary encoding of the manifest that will encode to the proper JSON format."""
        res = dict(
            resourceType="Task",
            text=dict(
                status="generated",
                div=self.__div_text__
            ),
            status=self.status,
            intent="order",
            authoredOn=self.authoredOn,
            focus=dict(reference=self.patientID),
            encounter=dict(reference=self.encounter),
            requestedPerformer=[dict(reference=dict(reference=self.performer))]
        )

        res["for"] = dict(reference=self.zipfile)

        if len(self.inputFiles) > 0:
            res["input"] = self.inputFiles.HL7_table

        if len(self.outputFiles) > 0:
            res["output"] = self.outputFiles.HL7_table

        if len(self.lastModified) > 0:
            res["lastModified"] = self.lastModified

        return res

    @classmethod
    def __HL7_timestamp__(cls):
        """Return a date/time string with the exact format HL7 FHIR wants."""
        tz = datetime.now(timezone.utc).astimezone().tzinfo
        ts = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S%z")
        ts = ts[:-2] + ':' + ts[-2:]
        return ts

    def make_archive_name(self, filetype="zip"):
        """Return a file name constructed from the manifest fields and the current time."""
        output = "RES" if self.status == "completed" else "NEW"

        fn = ".".join([clean_for_filename(s) for s in
                       (output, self.patientID, self.encounter,
                        self.performer, datetime.now().strftime("%s"),
                        filetype)
                       ])
        return fn

    @property
    def json(self):
        """A JSON serialisation of this manifest."""
        return json.dumps(self.__HL7_dict__(), indent=2)


def package_manifest(man: Manifest):
    """Package the manifest and the data files in it (inputFiles or outputFiles, depending) to a zip."""
    filename = man.make_archive_name()
    if man.status == "completed":
        files = man.outputFiles.files
    else:
        files = man.inputFiles.files

    filetest = [os.path.isfile(f) for f in files]
    missing = [x[0] for x in zip(files, filetest) if x[1] is False]

    if len(missing) > 0:
        print("Some files specified in the manifest not found when trying to package:")
        print(repr(missing))
        return

    print(f"Creating {filename}")
    man.zipfile = filename
    with zipfile.ZipFile(filename, 'w') as zout:
        print("Adding MANIFEST.json")
        zout.writestr("MANIFEST.json", man.json)
        for fn in files:
            print(f"Adding {fn}")
            zout.write(fn)

    print("Done.")
    return(filename)


### The command line tool:
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="teamplayManifest",
        description="Parse and package teamplay manifests"
    )
    parser.add_argument('verb', choices=[
        'printPerformer',
        'printInfo',
        'package',
        'extract'
    ])
    parser.add_argument('file', nargs='?', default="MANIFEST.json", help="Manifest or package to work on")
    args = parser.parse_args()

    manifest_file = args.file
    if manifest_file.endswith(".zip"):
        zf = zipfile.ZipFile(manifest_file, mode='r')
        manifest_name = [f for f in zf.namelist() if f.lower() == "manifest.json"]
        manifest_name.sort()
        if len(manifest_name) > 1:
            print(f"Found multiple manifests in {args.file}: {manifest_name}")
            print(f"Trying {manifest_name[0]} (because it sorted first)")
        manifest_file = zf.open(manifest_name[0])

    manifest = Manifest.from_file(manifest_file)

    match args.verb:
        case "printPerformer":
            print(manifest.performer)
            exit(0)
        case "printInfo":
            print(manifest)
            exit(0)
        case "package":
            if manifest_file.endswith(".zip"):
                print("Trying to create a package from a manifest inside a package.")
                print("If you really want this, extract the manifest and package it in two steps.")
                exit(0)
            package_manifest(manifest)
            exit(0)
        case 'extract':
            if os.path.isfile("MANIFEST.json"):
                print("MANIFEST.json already exists in current directory. Will not overwrite.")
                exit(1)
            with open("MANIFEST.json", "w") as mf:
                mf.write(manifest.json)
            exit(0)
