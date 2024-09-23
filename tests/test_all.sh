#!/bin/sh

# References
REF_HEADER='Manifest for [TEST1] @ [TIME1] on [MANIFEST-TESTER]'
REF_FILE1='testfile1.csv'
REF_FILE2='testfile2.vcf'
REF_PERFORMER='MANIFEST-TESTER'

# Manifest script
CMD="python3 ../../manifest.py"

## Cleanup
if [ -d "__TESTS" ]; then
    echo -e "[INFO]\t__TESTS exists, deleting"
    rm __TESTS/*
    rmdir __TESTS
fi


## Run the python module tests
## These also generate the zip file we test on further down
python3 test_mod.py


## Test the CLI tool

# A selection of tests that can work on both a zip file and an extracted manifest
# Works on $MANIFEST , which should be the name of a manifest or zip file.
test_manifest() {
    echo -e "[INFO]\tTesting $MANIFEST"
    header=`$CMD printInfo "$MANIFEST" | head -n1`
    if [ "$header" == "$REF_HEADER" ]; then
        echo -e "[ OK ]\tCLI Tool got a good header"
    else
        echo -e "[FAIL]\tCLI Tool got the wrong header"
        echo -e "\t Expected '$REF_HEADER'"
        echo -e "\t Got      '$header'"
        exit 1
    fi

    performer=`$CMD printPerformer $MANIFEST`
    if [ "$performer" == "$REF_PERFORMER" ]; then
        echo -e "[ OK ]\tCLI Tool got correct performer"
    else
        echo -e "[FAIL]\tCLI Tool got the wrong performer: Expected '$REF_PERFORMER' got '$performer'"
        exit 1
    fi


    f1=`$CMD printInfo $MANIFEST | grep "$REF_FILE1"`
    f2=`$CMD printInfo $MANIFEST | grep "$REF_FILE2"`

    if [ -n "$f1" ] && [ -n "$f2" ]; then
        echo -e "[ OK ]\tCLI Tool got the right files in Manifest"
    else
        echo -e "[FAIL]\tCLI Tool did not find '$REF_FILE1' and '$REF_FILE2' in output (below): "
        echo "$f1"
        echo "$f2"
        return 1
    fi

}

cd __TESTS

# Test the zip file
ZIPFILE=`ls *.zip | head -n1`
MANIFEST="$ZIPFILE"
test_manifest

# And the extracted manifest
$CMD extract $ZIPFILE
MANIFEST="MANIFEST.json"
test_manifest

# Re-package. The zip name contains the creation time and will be different.
echo -e "[INFO]\t Testing re-packaged file"
rm "$ZIPFILE"
touch "$REF_FILE1" "$REF_FILE2"
$CMD package
ZIPFILE=`ls *.zip | head -n1`
MANIFEST="$ZIPFILE"
test_manifest

rm "$ZIPFILE" "MANIFEST.json" "$REF_FILE1" "$REF_FILE2"
cd ..
rmdir __TESTS
