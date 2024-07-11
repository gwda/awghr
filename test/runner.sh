set -e

echo "===== $0: create virtual environment"
python3 -m venv ./test-venv/
source ./test-venv/bin/activate

echo "===== $0: install packages"
pip install --force-reinstall ./dist/*.whl

echo "===== $0: run tests"
for TEST in `cat ./test/tests.txt`; do
    python3 "./test/${TEST}.py"
    echo "===== $0: PASSED: ${TEST}"
done

echo "===== $0: DONE"
