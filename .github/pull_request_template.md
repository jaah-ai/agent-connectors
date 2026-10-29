<!-- One connector per PR, against `main`. CONTRIBUTING.md §8; how an accepted PR lands: §9. -->

Implements JGP-<N> / Fixes #<n>

## What changed

## Checklist (author)

- [ ] `python3 tools/check_connectors.py connectors/<name> --bundled` prints `ok`
- [ ] Manifest `version` bumped
- [ ] Tests cover the paths CONTRIBUTING.md §7 lists; coverage ≥ 80%
- [ ] README header table and behaviour sections current
- [ ] No secret, `.env`, generated file or third-party dependency committed

The reviewer posts the GOVERNANCE.md §3 checklist as a comment; a maintainer then lands the change at the source and
closes this PR with a link to the release that carries it.
