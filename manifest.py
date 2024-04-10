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
from typing import Self
import zipfile
import os.path
import re


def try_nested_key(d, key_list, fallback=""):
    for k in key_list:
        try:
            d = d[k]
        except (KeyError, TypeError):
            return fallback
    return d


def clean_for_filename(s):
    s = s.encode("ASCII", "replace").decode("ASCII")
    s = s.replace('?', '_')
    s = re.sub("[^-_()a-zA-Z0-9]", "_", s)
    return s


class FileAttachmentList:
    def __init__(self, files=None):
        super().__init__()
        self._files = []
        if files is not None:
            self.insert(files)

    def insert(self, f) -> None:
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
        return len(self._files)

    def __repr__(self):
        return "FileAttachmentList(" + repr(self._files) + ")"

    @property
    def files(self):
        return [f['Filename'] for f in self._files]

    @property
    def table(self):
        return self._files

    @property
    def HL7_table(self):
        return [
            dict(
                type=dict(text=f["Description"]),
                valueAttachment=dict(
                    contentType=f["MIME"],
                    url="file://" + f["Filename"])
            )
            for f in self._files]

    def __str__(self):
        text = ["\n  ".join((
            "-> " + " " + f["Filename"],
            "   MIME: " + f["MIME"],
            "   Description: " + f["Description"]
        )) for f in self._files]
        return "\n".join(text)


class Manifest(dict):
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

    def __fill_from_parsed(self, parsed: dict) -> Self:
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

    def __str__(self) -> str:
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
        attrs = ["status", "authoredOn", "zipfile", "patientID", "encounter",
                 "performer", "inputFiles", "outputFiles"]
        return ((attr, self.__getattribute__(attr)) for attr in attrs)

    @classmethod
    def from_file(cls, filename) -> Self:
        instance = cls()
        if 'read' in dir(filename):
            parsed = json.load(filename)
        else:
            parsed = json.load(open(filename, "r"))
        return instance.__fill_from_parsed(parsed)

    @classmethod
    def from_json(cls, json_text) -> Self:
        instance = cls()
        parsed = json.loads(json_text)
        return instance.__fill_from_parsed(parsed)

    # noinspection PyPep8Naming
    @classmethod
    def new(cls, patientID, encounter, performer, files):
        self = cls()
        self.patientID = patientID
        self.encounter = encounter
        self.performer = performer
        self.inputFiles = FileAttachmentList(files)
        return self

    def mark_done(self, out_files):
        self.status = "completed"
        self.lastModified = Manifest.__HL7_timestamp__()
        self.outputFiles = FileAttachmentList(out_files)
        return self

    @property
    def __div_text__(self) -> str:
        res = "<div xmlns='http://www.w3.org/1999/xhtml'>"
        res += "Output" if self.status == "completed" else "Input"
        res += f" task for {self.patientID}, created {self.authoredOn}"
        res += "</div>"
        return res

    def __HL7_dict__(self):
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
        tz = datetime.now(timezone.utc).astimezone().tzinfo
        ts = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S%z")
        ts = ts[:-2] + ':' + ts[-2:]
        return ts

    def make_archive_name(self, filetype="zip"):
        output = "RES" if self.status == "completed" else "NEW"

        fn = ".".join([clean_for_filename(s) for s in
                       (output, self.patientID, self.encounter,
                        self.performer, datetime.now().strftime("%s"),
                        filetype)
                       ])
        return fn

    @property
    def json(self) -> str:
        return json.dumps(self.__HL7_dict__(), indent=2)


def package_manifest(man: Manifest):
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
    with zipfile.ZipFile(filename, 'w') as zout:
        print("Adding MANIFEST.json")
        zout.writestr("MANIFEST.json", man.json)
        for fn in files:
            print(f"Adding {fn}")
            zout.write(fn)

    print("Done.")
    return(filename)


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
