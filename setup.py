from setuptools import setup, find_packages

setup(
    name="blinkymap",
    version="0.1.0",
    description="Pixel-tree 3D mapper for xLights — triangulate pixel positions from camera footage",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "opencv-python>=4.5.0",
        "numpy>=1.21.0",
        "requests>=2.26.0",
        "matplotlib>=3.4.0",
        "Pillow>=9.0.0",
    ],
    entry_points={
        "console_scripts": [
            "blinkymap=main:main",
        ],
    },
)
