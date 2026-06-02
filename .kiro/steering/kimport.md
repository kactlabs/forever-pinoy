---
inclusion: always
---

# KImport Rules

KImport means: bring a feature from the code in a `/sample` reference folder and integrate it with the existing project.

## Core Principles

- Import only the functional aspects of the code (logic, routes, models, utilities, etc.)
- Do NOT import the color theme, CSS theme, or visual styling from the sample
- Do NOT introduce a new framework or change the existing framework pattern
- Adapt the imported code to match the existing project's UI theme, framework conventions, and code style
- The `/sample` folder is reference-only and will be deleted after KImport is complete

## Process

1. Read and understand the feature in `/sample`
2. Identify the functional logic to bring over (endpoints, business logic, data models, etc.)
3. Integrate that logic into the existing project structure, following the project's current patterns
4. Preserve the existing project's UI theme, styling, and framework choices
5. Do NOT copy-paste stylesheets, theme files, or framework boilerplate from `/sample`
