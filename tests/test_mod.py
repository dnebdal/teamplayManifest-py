from datetime import datetime
import json
import os
import re
import sys

## Preparations
assert os.path.realpath(".").endswith("/tests")
modpath = re.sub('/tests$', "/", os.path.realpath("."))
sys.path.append(modpath)

os.makedirs("__TESTS")
os.chdir("__TESTS")

# Just touch the input files
_ = open("testfile1.csv", "w"); _.close()
_ = open("testfile2.vcf", "w"); _.close()

## Actual testing

from manifest import Manifest, package_manifest
infiles = [
  {'Filename':'testfile1.csv', 'Description':'Methylation', 'MIME':'text/csv'},
  {'Filename':'testfile2.vcf', 'Description':'Mutation', 'MIME':'text/tab-separated-values'}
]

mf = Manifest.new(patientID="TEST1", encounter="TIME1", performer="MANIFEST-TESTER", files=infiles)
assert type(mf) == Manifest

## Check that some basic fields are right
assert mf.DICOM_ref is None
assert mf.encounter == "TIME1"

# And the input files ...
f = mf.inputFiles.files
f.sort()
assert f == ['testfile1.csv', 'testfile2.vcf']

# ... and the timestamp, with the usual Python timezone fun.
# This allows a 5 sec difference between manifest creation and now.
hl7_ts = datetime.strptime(mf.authoredOn, '%Y-%m-%dT%H:%M:%S%z')
assert (datetime.now(tz = hl7_ts.tzinfo) - hl7_ts).total_seconds() < 5

print("[ OK ]\tManifest object looks sane")


## Test the packaging
package_manifest(mf, quiet=True)
assert os.path.isfile(mf.zipfile)

import zipfile
assert zipfile.is_zipfile(mf.zipfile)

zf = zipfile.ZipFile(mf.zipfile)
zf_files = [_.filename for _ in zf.infolist()]
zf_files.sort()
assert zf_files == ['MANIFEST.json', 'testfile1.csv', 'testfile2.vcf']

print("[ OK ]\tZip file looks sane")


## Test the generated JSON
jsondata = zf.open("MANIFEST.json", "r").read()
mf_json = json.loads(jsondata)
assert mf_json['resourceType'] == "Task"

mf2 = Manifest.from_json(jsondata)
assert mf.inputFiles.HL7_table == mf2.inputFiles.HL7_table

print("[ OK ]\tManifest read from JSON in zip file matches original object")

zf.close()
os.remove("testfile1.csv")
os.remove("testfile2.vcf")
os.chdir("..")

# Keep the zip file around for the CLI test
#os.removedirs("__TESTS")
#os.remove(mf.zipfile)