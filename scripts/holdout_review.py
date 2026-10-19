"""The holdout spot check in a web page on this machine only (127.0.0.1, never Railway).

    uv run python scripts/holdout_review.py --reference-check .cache/reference_check/round1 \\
        --reviewer "<your name>"
    uv run python scripts/holdout_review.py --reference-check ... --reviewer ... --full <case_id>

The same rules as ``verify_golden_case.py --spot-check``, on every tie-broken holdout draft in
``.cache/drafts/`` at once (or on ``--draft`` files): a ROBUST reference check is required, and
for each draft the seeded sample of ``SPOT_SAMPLE`` items is asked. The page is the labelling page
the classmates use, with the same blind cards: the claim and the IA text it rests on, never the
tool's decision or reasons. Yes, keep is ``verified``; No, drop is ``rejected``; Can't tell is
``unclear``. Answers are kept under ``.cache/holdout_review/`` as you go, so a closed tab loses
nothing. Send runs the spot check on each draft: a draft where you agree with the tool on every
sampled item is handed off to ``import_holdout_case.py``; a draft with a disagreement is refused
and named, and ``--full <case_id>`` then asks every item of that draft.

The server listens on 127.0.0.1 only, answers only requests addressed to that host (no DNS
rebinding) and runs with LangSmith tracing off. The page is plain English text, so the browser's
translate button works on it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from langsmith import tracing_context

from womm.config import REPO_ROOT
from womm.eval import ia_sources
from womm.eval import reference_check as rc
from womm.eval.drafting import GoldenDraft
from womm.eval.golden_review import KEPT_DECISIONS


def _load_verify():
    """scripts/verify_golden_case.py, loaded once (its error class must be the same object)."""
    if "verify_golden_case" in sys.modules:
        return sys.modules["verify_golden_case"]
    spec = importlib.util.spec_from_file_location(
        "verify_golden_case", Path(__file__).with_name("verify_golden_case.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_golden_case"] = module
    spec.loader.exec_module(module)
    return module


verify = _load_verify()

DRAFTS_DIR = REPO_ROOT / ".cache" / "drafts"
STATE_DIR = REPO_ROOT / ".cache" / "holdout_review"
API = "/api"
DECISION = {"keep": "verified", "drop": "rejected", "unsure": "unclear"}
# rejected and unclear need a note (golden_review.NOTE_REQUIRED); these stand in when none is given.
DEFAULT_NOTE = {
    "drop": "dropped in the holdout review",
    "unsure": "can't tell in the holdout review",
}


class Review:
    """The cards, the answers given so far and what Send does; the HTTP layer only calls this."""

    def __init__(
        self,
        drafts: dict[Path, GoldenDraft],
        *,
        reviewer: str,
        reference_dir: Path,
        full: str | None = None,
        out_dir: Path = verify.HANDOFF_DIR,
        state_dir: Path = STATE_DIR,
        ia_root: Path | None = None,
        today: dt.date | None = None,
    ) -> None:
        if not reviewer.strip():
            raise verify.VerifyError("a reviewer name is required (--reviewer)")
        self.reviewer, self.full, self.out_dir = reviewer.strip(), full, out_dir
        self.today = today or dt.date.today()
        self.reference = verify.robust_reference_check(reference_dir, list(drafts))
        if full:
            drafts = {p: d for p, d in drafts.items() if d.case_id == full}
            if not drafts:
                raise verify.VerifyError(f"no holdout draft {full} among the drafts given")
        if not drafts:
            raise verify.VerifyError(f"no holdout drafts in {DRAFTS_DIR}")
        self.drafts = drafts
        self.asked: dict[str, list] = {}
        for draft in drafts.values():
            if full:
                self.asked[draft.case_id] = draft.items()
            else:
                verify.require_settled(draft)
                self.asked[draft.case_id] = verify.spot_sample(draft, self.reference.spot_size)
        self.cards = self._cards(ia_root)
        self.lock = threading.Lock()
        self.state_path = state_dir / f"answers_{full or 'spot'}.json"
        self.answers: dict[str, dict[str, str]] = self._load()
        self.submitted = False

    def _cards(self, ia_root: Path | None) -> list[dict[str, Any]]:
        by_case = {d.case_id: d for d in self.drafts.values()}
        texts, rsb_texts = {}, {}
        for fixture in sorted({d.fixture for d in by_case.values()}):
            cached = ia_sources.load_cached_ia(fixture, ia_root)
            texts[fixture] = cached.full_text if cached else None
            rsb_texts[fixture] = cached.rsb_text if cached else None
        cards = []
        for case_id, items in self.asked.items():
            draft = by_case[case_id]
            for item in items:
                it = rc.Item(case_id, draft.fixture, item.item_id, rc.stratum(item) or "",
                             item.review.decision in KEPT_DECISIONS)  # fmt: skip
                cards.append(rc.card(item.item_id, it, by_case, texts, rsb_texts))
        ids = [c["id"] for c in cards]
        if len(set(ids)) != len(ids):
            raise verify.VerifyError("two drafts share an item id; review them one at a time")
        return cards

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        known = {c["id"] for c in self.cards}
        return {k: v for k, v in saved.items() if k in known}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.answers, fh, ensure_ascii=False, indent=1)

    def me(self) -> dict[str, Any]:
        return {"annotator": self.reviewer, "kind": rc.KIND, "items": self.cards,
                "answers": self.answers, "submitted": self.submitted}  # fmt: skip

    def answer(self, item_id: str, label: str, note: str) -> dict[str, str]:
        if item_id not in {c["id"] for c in self.cards}:
            raise LookupError(f"no item {item_id}")
        if label not in DECISION:
            raise ValueError(f"label must be one of {', '.join(DECISION)}")
        with self.lock:
            self.answers[item_id] = {"label": label, "note": note.strip()}
            self._save()
        return {"saved": item_id}

    def decisions(self, case_id: str) -> dict[str, dict[str, str]]:
        out = {}
        for item in self.asked[case_id]:
            a = self.answers[item.item_id]
            note = a["note"] or DEFAULT_NOTE.get(a["label"], "")
            out[item.item_id] = {
                "decision": DECISION[a["label"]],
                **({"note": note} if note else {}),
            }
        return out

    def submit(self) -> dict[str, Any]:
        """Each draft's outcome, one line each; nothing is written for a refused draft."""
        open_ = [c["id"] for c in self.cards if c["id"] not in self.answers]
        if open_:
            raise ValueError(f"still open: {', '.join(open_)}")
        lines, refused = [], []
        for path, draft in self.drafts.items():
            decisions = self.decisions(draft.case_id)
            try:
                if self.full:
                    case = verify.verify(draft, decisions, self.reviewer, self.today)
                    out = verify.write_handoff(case, path, self.reviewer, self.today, self.out_dir)
                else:
                    case, record = verify.spot_verify(draft, decisions, self.reviewer,
                                                      self.today, self.reference)  # fmt: skip
                    out = verify.write_handoff(case, path, self.reviewer, self.today, self.out_dir,
                                               spot_check=record)  # fmt: skip
                lines.append(f"{draft.case_id}: done, ready for the holdout import ({out.name}).")
            except verify.VerifyError as exc:
                refused.append(draft.case_id)
                lines.append(f"{draft.case_id}: not done. {exc}")
        if refused and not self.full:
            lines.append("For each draft not done, check every item: run this again with --full "
                         "<case_id>.")  # fmt: skip
        self.submitted = not refused
        return {"submitted": True, "done": len(self.drafts) - len(refused),
                "refused": refused, "message": "\n".join(lines)}  # fmt: skip


def make_handler(review: Review, page: str, host: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: Any) -> None:  # quiet: the terminal shows outcomes only
            pass

        def _send(self, status: int, body: Any, ctype: str = "application/json") -> None:
            data = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            raw = data.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _guard(self) -> bool:
            # Only this machine's own address: a page elsewhere cannot rebind its name to it.
            if self.headers.get("Host") != host:
                self._send(403, {"detail": "local only"})
                return False
            return True

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def do_GET(self) -> None:  # noqa: N802
            if not self._guard():
                return
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, page, "text/html")
            elif path == f"{API}/me":
                self._send(200, review.me())
            else:
                self._send(404, {"detail": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._guard():
                return
            path = urlsplit(self.path).path
            if path == f"{API}/session":
                self._send(200, review.me())
            elif path == f"{API}/submit":
                try:
                    with tracing_context(enabled=False):
                        result = review.submit()
                except ValueError as exc:
                    self._send(409, {"detail": str(exc)})
                    return
                print(result["message"], flush=True)
                self._send(200, result)
            else:
                self._send(404, {"detail": "not found"})

        def do_PUT(self) -> None:  # noqa: N802
            if not self._guard():
                return
            path = urlsplit(self.path).path
            prefix = f"{API}/answers/"
            if not path.startswith(prefix):
                self._send(404, {"detail": "not found"})
                return
            body = self._body()
            try:
                result = review.answer(unquote(path[len(prefix):]), str(body.get("label") or ""),
                                       str(body.get("note") or ""))  # fmt: skip
            except LookupError as exc:
                self._send(404, {"detail": str(exc)})
            except ValueError as exc:
                self._send(422, {"detail": str(exc)})
            else:
                self._send(200, result)

    return Handler


def serve(review: Review, port: int = 0) -> ThreadingHTTPServer:
    """The server, bound to 127.0.0.1 (``port`` 0 picks a free one); not started."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    host = f"127.0.0.1:{server.server_address[1]}"
    page = rc.render_hosted_page("holdout", api=API, title="Holdout check")
    server.RequestHandlerClass = make_handler(review, page, host)
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference-check", type=Path, required=True,
                        help="the reference-check round dir that scores ROBUST")  # fmt: skip
    parser.add_argument("--reviewer", required=True, help="your name, recorded in the handoffs")
    parser.add_argument("--draft", type=Path, action="append",
                        help="holdout draft(s); default every *.yaml in .cache/drafts/")  # fmt: skip
    parser.add_argument("--full", metavar="CASE_ID", help="ask every item of this one draft")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=verify.HANDOFF_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = args.draft or sorted(DRAFTS_DIR.glob("*.yaml"))
    try:
        with tracing_context(enabled=False):
            drafts = {p: verify.load_holdout_draft(p) for p in paths}
            review = Review(drafts, reviewer=args.reviewer, reference_dir=args.reference_check,
                            full=args.full, out_dir=args.out_dir, ia_root=args.ia_root)  # fmt: skip
    except verify.VerifyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    server = serve(review, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    what = (
        f"every item of {args.full}"
        if args.full
        else f"{len(review.cards)} items over {len(review.drafts)} drafts"
    )
    print(f"holdout review ({what}): {url}\nCtrl+C stops it; answers are kept.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
