# Contributing

overseer is a hobby project, maintained on a best-effort basis. Issues and pull requests are welcome; the
maintainer reviews and merges them when there is time, and makes no promise about when.

## Reporting a problem

Open an issue with:

- what you ran and what happened (the command's output);
- your Windows version, Python version, display scaling and the game's resolution (`f1_res.ini` in `instance/`);
- the output of `python -m f1.verify_exe` and `python -m f1.instance check`;
- for a failed run, the end of its `runs/<run>/events.jsonl` (look it over first: it holds your folder paths).

A different edition of the game is worth an issue too: the SHA-256 of its `FALLOUTW.EXE` and where it came from (GOG,
another language, another store). The bot refuses every exe it does not know; a hash is the first step to knowing one.

## Pull requests

- **Small fixes** (a bug, a clearer message, a test, the docs): send them straight away.
- **Larger changes** (a new module, a change to how actions or routes work, new dependencies): open an issue first, so
  the approach can be agreed before the work.
- **Routes** (`f1/routes.py`, `f1/idealist.py` and the late-game modules) are merged only after the maintainer has run
  them on the live game; an offline test cannot tell whether a route still plays. Say in the PR what you ran and what
  the run's events showed.
- Before sending, run the same checks as CI:

  ```
  ruff check .
  ruff format --check .
  python -m pytest -q
  ```

- Write a fact about the game with how it was measured (static analysis of its files, or the running game), not
  when. Facts about the exe (addresses, struct layouts, hashes) are fine; quoting another project's source code is
  not.

## What must never go into the repository

No game files and nothing made from them: no DATs, art, sound, extracted text or `knowledge.json`, savegames,
screenshots or captures, exe bytes beyond the fingerprints `f1/engine_map.py` checks. The `.gitignore` keeps the
bot's own folders out (`instance/`, `extracted/`, `runs/`, `saves/`, `captures/`); keep it that way.

## License

By sending a contribution you agree that it is licensed under the project's license, GPL-3.0-or-later
([LICENSE](LICENSE)).
