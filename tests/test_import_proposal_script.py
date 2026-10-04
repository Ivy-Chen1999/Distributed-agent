import importlib.util
import json
import re
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from womm.config import REPO_ROOT
from womm.data import cellar
from womm.data.fixtures import load_fixture
from womm.data.ia_index import IA_FIELDS, load_ia_index

spec = importlib.util.spec_from_file_location(
    "import_proposal", REPO_ROOT / "scripts/import_proposal.py"
)
import_proposal = importlib.util.module_from_spec(spec)
sys.modules["import_proposal"] = import_proposal
spec.loader.exec_module(import_proposal)

FIXTURES = Path(__file__).parent / "fixtures"
DATA_ACT = (FIXTURES / "com2022_68_sample.xhtml").read_bytes()
LISTING = (FIXTURES / "cellar_300_com2022_454.html").read_text()
CELEX = "52022PC0068"
CELEX_URL = f"https://publications.europa.eu/resource/celex/{CELEX}"


@pytest.fixture
def serve(monkeypatch):
    """Serve ``body`` for the CELEX resource and count downloads; no network."""
    calls: list[str] = []

    def install(body: bytes = DATA_ACT):
        def fake_main(celex, **_):
            calls.append(celex)
            return f"https://publications.europa.eu/resource/celex/{celex}", body

        def fake_fetch(url, **_):
            calls.append(url)
            return body

        monkeypatch.setattr(import_proposal, "fetch_main_document", fake_main)
        monkeypatch.setattr(import_proposal, "fetch_document", fake_fetch)
        return calls

    return install


def _run(tmp_path, *args) -> int:
    return import_proposal.main([*args, "--root", str(tmp_path)])


def test_data_act_sample_imports_to_a_loadable_fixture(tmp_path, serve):
    serve()
    assert _run(tmp_path, CELEX, "--id", "data_act") == 0
    out = tmp_path / "data_act"
    fixture = load_fixture(out)
    version = fixture.version("com2022_68")
    assert fixture.regulation.title == "Data Act"
    assert str(version.date) == "2022-02-23"
    assert [p.article for p in version.provisions] == [str(n) for n in range(1, 43)]
    assert version.provisions[8].provision_key == "data_act/proposal/art/9"
    art9 = fixture.sources["com2022_68/art_9"]
    assert art9.title == "COM(2022) 68, Article 9: Compensation for making data available"
    memo = [s for s in fixture.sources.values() if s.kind == "memorandum"]
    assert [s.source_id.rsplit("/", 1)[1] for s in memo] == [
        "context",
        "legal_basis",
        "other_elements",
    ]
    assert "3.2. Impact assessment" in memo[0].stripped_sections
    assert not any("impact assessment" in s.text.casefold() for s in memo)
    pins = json.loads((out / "downloads.json").read_text())
    assert pins == {CELEX_URL: cellar.sha256(DATA_ACT)}
    config = yaml.safe_load((out / "import.yaml").read_text())
    assert config["document_url"] == CELEX_URL and config["regulation_id"] == "data_act"


def test_reimport_uses_import_yaml_and_is_idempotent(tmp_path, serve):
    calls = serve()
    _run(tmp_path, CELEX, "--id", "data_act")
    before = {p.name: p.read_bytes() for p in (tmp_path / "data_act").iterdir()}
    assert _run(tmp_path, CELEX) == 0  # found through import.yaml, no --id needed
    assert calls[-1] == CELEX_URL  # the pinned document_url, not a new resolution
    after = {p.name: p.read_bytes() for p in (tmp_path / "data_act").iterdir()}
    assert after == before


def test_scenarios_from_import_yaml(tmp_path, serve):
    serve()
    _run(tmp_path, CELEX, "--id", "data_act")
    path = tmp_path / "data_act" / "import.yaml"
    config = yaml.safe_load(path.read_text())
    config["scenarios"] = [
        {
            "scenario_id": "eval_compensation",
            "description": "Compensation for B2B data sharing (Art 8-9).",
            "articles": ["8", "9"],
            "ia_reference": None,
        }
    ]
    path.write_text(yaml.safe_dump(config))
    assert _run(tmp_path, CELEX) == 0
    scenario = load_fixture(tmp_path / "data_act").scenario("eval_compensation")
    assert scenario.kind == "evaluation" and scenario.before_version is None
    assert scenario.provision_keys == ["data_act/proposal/art/8", "data_act/proposal/art/9"]

    config["scenarios"][0]["articles"] = ["8", "99"]
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(import_proposal.ImportFailed, match=r"52022PC0068: .*\['99'\]"):
        _run(tmp_path, CELEX)


def test_leak_fails_naming_the_section_and_writes_nothing(tmp_path, serve):
    leaky = DATA_ACT.replace(
        b"<span>Reasons for and objectives of the proposal</span>",
        b"<span>Reasons for and objectives of the proposal</span>"
        b"</p><p class='Normal'><span>The preferred option costs little.</span>",
        1,
    )
    assert leaky != DATA_ACT
    serve(leaky)
    with pytest.raises(
        import_proposal.ImportFailed,
        match=r"(?s)52022PC0068: 1 IA marker.*Reasons for and objectives.*preferred option",
    ):
        _run(tmp_path, CELEX, "--id", "data_act")
    assert not (tmp_path / "data_act").exists()


LEAKY = DATA_ACT.replace(
    b"<span>Reasons for and objectives of the proposal</span>",
    b"<span>Reasons for and objectives of the proposal</span>"
    b"</p><p class='Normal'><span>The preferred option costs little. Data is an asset.</span>",
    1,
)


def _write_config(tmp_path, **settings) -> Path:
    out = tmp_path / "data_act"
    out.mkdir()
    path = out / "import.yaml"
    path.write_text(yaml.safe_dump({"celex": CELEX, "regulation_id": "data_act", **settings}))
    return path


def test_redaction_from_import_yaml_is_applied_and_recorded(tmp_path, serve):
    assert LEAKY != DATA_ACT
    serve(LEAKY)
    _write_config(
        tmp_path,
        redactions=[
            {
                "section": "1.1.",
                "sentence": "The preferred option",
                "reason": "states an IA conclusion",
            }
        ],
    )
    assert _run(tmp_path, CELEX) == 0
    out = tmp_path / "data_act"
    memo = [s for s in load_fixture(out).sources.values() if s.kind == "memorandum"]
    context = memo[0]
    assert "Data is an asset." in context.text
    assert "preferred option" not in context.text.casefold()
    assert context.redactions == [
        "1.1. Reasons for and objectives of the proposal: states an IA conclusion"
    ]
    assert all(s.redactions == [] for s in memo[1:])
    assert "preferred option" not in (out / "sources.json").read_text().casefold()
    config = yaml.safe_load((out / "import.yaml").read_text())
    assert config["redactions"][0]["reason"] == "states an IA conclusion"


def test_stale_redaction_fails_and_writes_nothing(tmp_path, serve):
    serve(DATA_ACT)  # the sentence is not in the document
    path = _write_config(
        tmp_path,
        redactions=[{"section": "1.1.", "sentence": "The preferred option", "reason": "r"}],
    )
    with pytest.raises(
        import_proposal.ImportFailed, match=r"(?s)52022PC0068: redaction failed.*not found"
    ):
        _run(tmp_path, CELEX)
    assert [p.name for p in path.parent.iterdir()] == ["import.yaml"]


# Synthetic identifiers (tests/fixtures/ia_index_sample.yaml style); no real IA ids in tests.
SAMPLE_INDEX = FIXTURES / "ia_index_sample.yaml"
IA_ARGS = ("--ia-celex", "51999SC0902", "--ia-date", "1999-03-04", "--rsb-ref", "SEC(1999) 903")


def test_ia_identifiers_go_only_to_the_private_index(tmp_path, serve):
    serve()
    index = tmp_path / "private" / "ia_index.yaml"
    index.parent.mkdir()
    index.write_text(SAMPLE_INDEX.read_text())
    assert _run(tmp_path, CELEX, "--id", "data_act", *IA_ARGS, "--ia-index", str(index)) == 0
    entries = load_ia_index(index)
    assert set(entries) == {"sample_act", "sample_directive", "data_act"}  # others kept
    assert entries["data_act"].model_dump(mode="json") == {
        "celex": CELEX,
        "ia_celex": "51999SC0902",
        "ia_date": "1999-03-04",
        "rsb_ref": "SEC(1999) 903",
    }
    for path in (tmp_path / "data_act").iterdir():
        text = path.read_text()
        assert not any(k in text for k in IA_FIELDS), path.name
        assert "51999SC0902" not in text and "SEC(1999)" not in text, path.name

    # A later run that passes one flag updates that field and keeps the others.
    assert _run(tmp_path, CELEX, "--rsb-ref", "SEC(1999) 904", "--ia-index", str(index)) == 0
    record = load_ia_index(index)["data_act"]
    assert (record.ia_celex, record.rsb_ref) == ("51999SC0902", "SEC(1999) 904")


def test_bad_ia_celex_is_rejected_before_anything_is_written(tmp_path, serve):
    calls = serve()
    index = tmp_path / "ia_index.yaml"
    with pytest.raises(import_proposal.ImportFailed, match="52022PC0068: bad IA identifiers"):
        _run(
            tmp_path,
            CELEX,
            "--id",
            "data_act",
            "--ia-celex",
            "SWD(1999) 2",
            "--ia-index",
            str(index),
        )
    assert calls == [] and not index.exists() and not (tmp_path / "data_act").exists()


def test_ia_fields_in_a_public_import_yaml_are_refused(tmp_path, serve):
    serve()
    _write_config(tmp_path, ia_celex="51999SC0902")
    with pytest.raises(import_proposal.ImportFailed, match=r"\['ia_celex'\] must not be in"):
        _run(tmp_path, CELEX)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (DATA_ACT.replace(b'class="Titrearticle', b'class="Unknownheading'), "no articles found"),
        (
            DATA_ACT.replace(b"<span>Article 29</span>", b"<span>Article 30</span>"),
            r"duplicate article numbers \['30'\]",
        ),
    ],
)
def test_bad_numbering_fails_naming_the_celex(tmp_path, serve, body, message):
    assert body != DATA_ACT
    serve(body)
    with pytest.raises(import_proposal.ImportFailed, match=f"52022PC0068: {message}"):
        _run(tmp_path, CELEX, "--id", "data_act")
    assert not (tmp_path / "data_act").exists()


def test_unrecognisable_300_listing_fails_naming_the_celex(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(300, text=LISTING.replace("_ACT_part1", "_other_part1"))

    client = httpx.Client(transport=httpx.MockTransport(handler))

    def fake_main(celex, **kw):
        return cellar.fetch_main_document(celex, cache_dir=tmp_path / "cache", client=client)

    monkeypatch.setattr(import_proposal, "fetch_main_document", fake_main)
    with pytest.raises(
        import_proposal.ImportFailed, match="52022PC0454: .*no single main document"
    ):
        _run(tmp_path, "52022PC0454", "--id", "cra")
    assert not (tmp_path / "cra").exists()


def test_first_import_needs_an_id(tmp_path, serve):
    serve()
    with pytest.raises(import_proposal.ImportFailed, match="first import needs --id"):
        _run(tmp_path, CELEX)


def test_refuses_to_overwrite_a_fixture_it_did_not_import(tmp_path, serve):
    serve()
    (tmp_path / "ai_act").mkdir()
    with pytest.raises(import_proposal.ImportFailed, match="refusing to overwrite"):
        _run(tmp_path, CELEX, "--id", "ai_act")


def test_changed_upstream_document_fails(tmp_path, serve):
    serve()
    _run(tmp_path, CELEX, "--id", "data_act")
    sources = (tmp_path / "data_act" / "sources.json").read_bytes()
    serve(DATA_ACT.replace(b"Compensation", b"Remuneration", 1))
    with pytest.raises(import_proposal.ImportFailed, match="upstream documents changed"):
        _run(tmp_path, CELEX)
    assert (tmp_path / "data_act" / "sources.json").read_bytes() == sources


def test_not_a_com_celex(tmp_path, serve):
    serve()
    with pytest.raises(import_proposal.ImportFailed, match="not a COM proposal CELEX"):
        _run(tmp_path, "32024R1689", "--id", "x")


COMMITTED = sorted(p.parent.name for p in (REPO_ROOT / "data/fixtures").glob("*/import.yaml"))


def test_at_least_ten_committed_imports():
    assert len(COMMITTED) >= 10, COMMITTED


@pytest.mark.parametrize("name", COMMITTED)
def test_committed_imports_load_and_are_complete(name):
    out = REPO_ROOT / "data" / "fixtures" / name
    config = yaml.safe_load((out / "import.yaml").read_text())
    fixture = load_fixture(out)
    assert fixture.regulation.regulation_id == name == config["regulation_id"]
    (version,) = fixture.regulation.versions
    numbers = [p.article for p in version.provisions]
    assert numbers == [str(n) for n in range(1, len(numbers) + 1)]
    assert all(p.provision_key == f"{name}/proposal/art/{p.article}" for p in version.provisions)
    pins = json.loads((out / "downloads.json").read_text())
    assert list(pins) == [config["document_url"]]
    memo = [s for s in fixture.sources.values() if s.kind == "memorandum"]
    assert memo and all(s.stripped_sections for s in memo)
    from womm.data.memorandum import LEAK_MARKERS

    allowed = [" ".join(a.split()).casefold() for a in config.get("leak_allow") or []]
    for s in memo:
        text = " ".join(s.text.split()).casefold()
        for phrase in allowed:  # reviewed exact phrases, e.g. another instrument's IA
            text = text.replace(phrase, " ")
        assert not any(m in text for m in LEAK_MARKERS), s.source_id
    # Every redaction entry is recorded once, by section and reason, on some memorandum source.
    recorded = sorted(r for s in memo for r in s.redactions)
    assert len(recorded) == len(config.get("redactions") or [])
    # IA identifiers live only in the gitignored evals/private/ia_index.yaml (holdout safety).
    assert not any(k in config for k in IA_FIELDS), name
    raw = (out / "import.yaml").read_text()
    assert not re.search(r"\b5\d{4}SC\d{4}|SEC\(\d{4}\)|SWD\(\d{4}\)", raw), name
