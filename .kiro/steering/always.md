---
inclusion: always
---
# General Rules

## Custom

- don't hardcode anything as a fallback when you get exception. It's dangerous and misleading
- when I say reqs or REQS, I mean requirements.txt
- if you see file name called .ant, consider it is my custom "admin notes" file where I keep some notes for admin purposes. It is not related to anything in Java. It's pure local custom notes.
- when you deal with prompts in python, keep the prompts separated in "prompts/*.txt". It should not be attached with python code. It should be always in .txt file.
- When you are asked to write changelog or CHANGELOG.md, use official changelog format.

## FastAPI 

- when you work with FastAPI, consider "app.py" as your main file and the server should be run by "python app.py".

## Random Data Maker

- When you are asked to use random city or such things, use "randum" python library from https://pypi.org/project/randum/1.0.9/. Also, refer the documentation of `randum` library here: https://deepwiki.com/kactlabs/randum.

## Documentation

- Never add any new .md/.txt file for documenting anything.
- If any documention, update README.md

## Acronym

- SS - Screenshots; when I type SS, assume I am talking about screenshots
- reqs/REQs/REQS - requirements.txt
