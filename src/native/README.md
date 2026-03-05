# Native Runtime Scaffold (v5.69.0)

This folder holds the optional C++ acceleration path for scanner and overlay workers.

Current status:
- Python runtime remains the default.
- Native module loading is opt-in via config flags.
- If native module load or scanner creation fails, runtime falls back to Python scanner.

Planned native extension module name:
- `tli_native`

Build inputs are under `src/native/cpp/`.
