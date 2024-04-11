# CUDL Search API

## Prerequisites

It's recommended that you install virtualenv so you don't clutter up your system python or cause problems with other projects.

1. Make sure that python3 is installed on your machine by running `which python3`

2. Install Virtualenv

All commands should be run at the root level of the repository.

### OSX

1. Install Homebrew and virtualenv: 

```
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew install virtualenv
```

If all has gone well, you'll now see a `venv` directory at the root level of the repository.

2. Create an isolated python environment for this project

```
    virtualenv venv -p $(which python3)
```

3. Activate the local python

```
    source venv/bin/activate
```

4. Confirm that python3 and pip are the ones in that environment using which (*i.e.* the paths revealed with `which python3` and `which pip3` should be within this project and not the main system)

5. Install the required modules:

```
    pip3 install -r requirements.txt
```

## Starting the API

Activate the local python environment and run uvicorn.

```
    source venv/bin/activate
    uvicorn main:app --reload --log-config=log_conf.yaml
```