#!/usr/bin/env python

from os.path import abspath, dirname
from setuptools import find_packages, setup
import pathlib

from setuptools import setup

import pkg_resources

with pathlib.Path('requirements.txt').open() as requirements_txt:
    install_reqs = [
        str(requirement)
        for requirement
        in pkg_resources.parse_requirements(requirements_txt)
    ]

here = abspath(dirname(__file__))

setup(
    name='aps1toconnect',
    author='CloudBlue',
    keywords='aps 1.2 to connect migation',
    packages=find_packages(),
    description='A command line tool that allows automating APS 1.2 to Connect migration',
    url='https://github.com/cloudblue/aps1toconnect',
    license='Apache Software License',
    install_requires=install_reqs,
    entry_points={
        'console_scripts': [
            'aps1toconnect = aps1toconnect.migrator:main',
        ]
    },
    classifiers=[
        'Development Status :: 4 - Beta',

        'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries :: Python Modules',

        'License :: OSI Approved :: Apache Software License',

        'Programming Language :: Python',
        'Programming Language :: Python :: 3.8',

        'Operating System :: OS Independent',
        'Operating System :: POSIX',
        'Operating System :: MacOS',
        'Operating System :: Unix',
    ],
)
