<!-- One connector per PR, against `develop`. CONTRIBUTING.md §8. -->

Implements JGP-<N> / Fixes #<n>

## What changed

## Checklist (author)

- [ ] `python3 tools/check_connectors.py connectors/<name> --sdk-version <current SDK>` prints `ok`
- [ ] Manifest `version` bumped
- [ ] Tests cover the paths CONTRIBUTING.md §7 lists; coverage ≥ 80%
- [ ] README header table and behaviour sections current
- [ ] No secret, `.env` or generated output committed

The reviewer posts the GOVERNANCE.md §3 checklist as a comment before merging.
