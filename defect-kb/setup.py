"""Package metadata — not published yet, prepared for future pip distribution."""

from setuptools import setup, find_packages

setup(
    name="defect-kb",
    version="0.2.0",
    description="Project-agnostic defect knowledge base CLI for AI coding assistants",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "chromadb>=0.5.0",
        "openai>=1.30.0",
        "pydantic>=2.7.0",
        "pyyaml>=6.0",
    ],
    extras_require={
        "local": ["sentence-transformers>=2.7.0"],
    },
    entry_points={
        "console_scripts": [
            "defect-kb=cli:main",
        ],
    },
)
