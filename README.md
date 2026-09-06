# Hackathon project

## Python environment

This project uses a local virtual environment in `.venv`. Create and activate it
with:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The dependencies include:

- `pandas` for reading and processing CSV files
- `langchain` for language-model application components
- `langgraph` for stateful, graph-based workflows

The standard-library `csv` module is available without an additional install.
