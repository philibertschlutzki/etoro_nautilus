import subprocess

def run_test():
    with open('automation/tests/test_precision_mismatch.py', 'r') as f:
        content = f.read()
    print("test_precision_mismatch.py contents read")

run_test()
