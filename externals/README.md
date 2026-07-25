# External source references

This directory records independently versioned source that is useful for
inspection but cannot be a mandatory public submodule.

## fieldextra

`fieldextra.lock` pins the private COSMO-ORG source revision inspected by this
workflow. Developers with repository access can materialize the reference
checkout at the ignored top-level `fieldextra/` path:

```bash
./scripts/bootstrap_externals.sh --with-fieldextra
```

Normal workflow runs use the verified operational fieldextra executable.
Fetching this source reference is optional, and compiling it is outside the
normal workflow.
