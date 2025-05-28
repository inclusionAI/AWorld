# Publishing to PyPI

This guide will help you publish the `mcp_openapi` package to PyPI so that users can install it with `pip install mcp_openapi`.

## Prerequisites

Make sure you have the following tools installed:

```bash
# Python 3.11 or higher is required
python --version  # Should show Python 3.11.x or higher

# Install required tools
pip install build twine
```

## Build the Package

1. Navigate to the root directory of the package (where `setup.py` is located):

```bash
cd mcp_openapi
```

2. Build the package:

```bash
python -m build
```

This will create two files in the `dist/` directory:
- A source distribution (.tar.gz)
- A wheel distribution (.whl)

## Test the Package Locally (Optional)

You can test your package locally before uploading to PyPI:

```bash
pip install dist/mcp_openapi-0.1.0-py3-none-any.whl
```

## Upload to Test PyPI (Recommended)

Before uploading to the real PyPI, you can test the upload process using Test PyPI:

1. Register an account on [Test PyPI](https://test.pypi.org/account/register/)

2. Upload your package:

```bash
twine upload --repository-url https://test.pypi.org/legacy/ dist/*
```

3. Install from Test PyPI:

```bash
pip install --index-url https://test.pypi.org/simple/ mcp_openapi
```

## Upload to PyPI

Once you're ready to publish to the real PyPI:

1. Register an account on [PyPI](https://pypi.org/account/register/)

2. Upload your package:

```bash
twine upload dist/*
```

## Update the Package

When you want to update the package:

1. Update the version number in `mcp_openapi/mcp_openapi/__init__.py` and `setup.py`
2. Rebuild the package
3. Upload the new version to PyPI

## Automation with GitHub Actions (Optional)

You can automate the publishing process using GitHub Actions. Create a file at `.github/workflows/publish.yml` with the following content:

```yaml
name: Publish to PyPI

on:
  release:
    types: [created]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.11'
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install build twine
    - name: Build and publish
      env:
        TWINE_USERNAME: ${{ secrets.PYPI_USERNAME }}
        TWINE_PASSWORD: ${{ secrets.PYPI_PASSWORD }}
      run: |
        cd mcp_openapi
        python -m build
        twine upload dist/*
```

Then, when you create a new release on GitHub, it will automatically build and publish to PyPI. 