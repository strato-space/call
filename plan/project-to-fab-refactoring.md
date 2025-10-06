# Project to Fab refactoring plan

## call repo: remame project to fab in each function signature in whole repo

### [ ] pls check

- [ ] lib
- [ ] repo.db and repo_fs.py/repo_db.py
- [ ] actions
- [ ] mcp
- [ ] cli

### [ ] update docs

### [ ] update tests

### [ ] make all tests

## agent repo changes:

[ ] rename projects.yaml => fabs.yaml

[ ] in fabs.yaml attr projects => fabs

[ ] each project.md in subrectories rename to fab.mb

[ ] update docs- check all `projects.yaml` or `project.md` strings

[ ] if found `project:` attr in any card rename it to `fab:`

## prompt repo changes

[ ] open each prompt `card` in `draft` and ready foldeers and replace `project:` attr in any card rename it to `fab:` attr 

[ ]