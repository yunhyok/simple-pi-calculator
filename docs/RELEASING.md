# Releasing Simple PI Calculator

This project ships a single artifact per release: a Windows installer
(`SimplePICalculator-Setup-<version>.exe`) built by GitHub Actions and attached to a GitHub
Release. Versioning and CI behaviour are specified in `docs/DESIGN.md` §7.3–7.4; this page is the
step-by-step checklist.

## Version policy

* The single source of truth for the version is
  `src/simple_pi_calculator/__init__.py: __version__ = "X.Y.Z"` (SemVer).
* `pyproject.toml` reads this attribute dynamically — you never edit the version in two places.
* A release is a git tag `vX.Y.Z`. CI **fails the build** if the tag does not match
  `__version__` exactly, so bump the version first.
* Non-tag builds (pushes to `main`, manual `workflow_dispatch`) still build and upload an
  installer/zip as a CI artifact, but do not create a GitHub Release.

## Steps to cut a release

1. **Update the version.**
   Edit `src/simple_pi_calculator/__init__.py` and bump `__version__` to the new `X.Y.Z`.

2. **Update the changelog (optional but recommended).**
   If `CHANGELOG.md` exists, add a section for the new version. The release workflow uses it as
   the GitHub Release body when present; otherwise GitHub's auto-generated notes are used.

3. **Commit the version bump.**

   ```bash
   git add src/simple_pi_calculator/__init__.py CHANGELOG.md
   git commit -m "Release vX.Y.Z"
   git push origin main
   ```

   Wait for the `CI` and `Build Windows installer` workflows on `main` to go green before
   tagging — the tag build re-runs the same test suite, but catching a failure on `main` first is
   faster to fix.

4. **Tag the release.**

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

   Pushing the tag triggers `.github/workflows/build-windows.yml`, which:
   * runs the test suite (`pytest -q`, `QT_QPA_PLATFORM=offscreen`) on `windows-latest`;
   * verifies the tag equals `v<__version__>` (fails fast on a mismatch);
   * builds the PyInstaller onedir bundle and runs its `--self-test` smoke test;
   * compiles the Inno Setup installer with `/DMyAppVersion=X.Y.Z`;
   * uploads the installer `.exe` and a zipped onedir bundle as workflow artifacts;
   * creates a GitHub Release for the tag and attaches both files.

5. **Verify the release.**
   * Check the [Actions tab](../../actions) for a green run on the tag.
   * Open the new entry on the [Releases page](../../releases), confirm both files are attached,
     and download the installer once to sanity-check it installs and launches.

6. **Announce / done.** No further steps — the installer link on the Releases page (and in
   `README.md`) is the durable download URL for that version.

## Building a release installer locally

To reproduce a CI build on your own Windows machine (e.g. to debug a packaging issue before
tagging), use the local build script:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Version 0.1.0
```

This creates a fresh virtual environment, installs the package, runs the tests, builds the
PyInstaller bundle, and compiles the installer with Inno Setup 6 (must be installed locally;
see https://jrsoftware.org/isinfo.php). See `packaging/build_windows.ps1` for details and the
`-SkipTests` flag.

## Hotfix releases

For a patch release off an already-tagged version, branch from the tag, cherry-pick the fix,
bump the patch version, and follow the same steps above from a release branch merged back
into `main`. There is no separate hotfix tooling — the same tag-triggered workflow handles it.

## What never changes between releases

* The Inno Setup `AppId` GUID in `packaging/installer.iss` — changing it would make Windows treat
  upgrades as a different, side-by-side application and break existing installs' upgrade path.
* The per-user auto-save location (`%APPDATA%\SimplePICalculator`) — the uninstaller deliberately
  leaves it in place so upgrading (uninstall old version, install new) does not lose a user's
  in-progress project state.
