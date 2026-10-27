import pytest
from calculator import divide, add

def test_add():
    assert add(2, 3) == 5

def test_divide():
    assert divide(6, 2) == 3.0

def test_divide_by_zero():
    assert divide(5, 0) == 0


import time
def test_timeout():
    time.sleep(5)
