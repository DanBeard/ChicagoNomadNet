"""
Setup script for zimfast - Fast ZIM file reader

Build with:
    pip install .

For debug build with Address Sanitizer:
    ZIMFAST_ASAN=1 pip install .

Requires:
    - libzim-dev (apt install libzim-dev)
    - pybind11 (pip install pybind11)
    - C++17 compiler
"""

import os
from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

# Check for ASAN build
use_asan = os.environ.get("ZIMFAST_ASAN", "0") == "1"

extra_compile_args = ["-g"]  # Always include debug symbols
extra_link_args = []

if use_asan:
    print("*** Building with Address Sanitizer (ASAN) ***")
    extra_compile_args += ["-fsanitize=address", "-fno-omit-frame-pointer", "-O1"]
    extra_link_args += ["-fsanitize=address"]
else:
    extra_compile_args += ["-O2"]

ext_modules = [
    Pybind11Extension(
        "zimfast",
        ["zimfast.cpp"],
        libraries=["zim"],
        cxx_std=17,
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
    ),
]

setup(
    name="zimfast",
    version="0.1.0",
    author="ChicagoNomadNet",
    description="Fast ZIM file reader with cluster-order iteration",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
    python_requires=">=3.8",
)
