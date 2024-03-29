import subprocess
from setuptools import setup

with open('requirements.txt', encoding="utf-16") as f:
    requirements = f.read().splitlines()

setup(
    name='DataToolkit',
    url='https://github.com/coolneighbors/DataToolkit.git',
    author='Aaron Meisner',
    author_email='aaron.meisner@noirlab.edu',
    packages=['DataToolkit'],
    python_requires='>=3.9',
    install_requires=requirements,
    version='1.0',
    license='MIT',
    description='The Cool Neighbors data analysis GitHub repository.',
    long_description=open('README.md').read(),
)

# This fixes an import bug with the python-magic and python-magic-bin packages.
# https://github.com/zooniverse/panoptes-python-client/issues/264

import platform

if platform.system() == 'Windows':
    subprocess.call(['pip', 'install', 'python-magic'])
    subprocess.call(['pip', 'uninstall', 'python-magic-bin'])
    subprocess.call(['pip', 'install', 'python-magic-bin'])