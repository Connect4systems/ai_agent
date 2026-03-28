from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

with open("README.md") as f:
    long_description = f.read()

setup(
    name="ai_assistant",
    version="0.0.1",
    description="AI Chatbot for ERPNext v15 — reads DB, workflows, and user permissions",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Connect4systems",
    author_email="info@connect4systems.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
