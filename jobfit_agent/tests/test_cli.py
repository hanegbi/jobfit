from jobfit_agent import cli

JOBS = {"jobs": [{"id": "j1", "company": "Acme", "title": "Backend", "best_score": 90},
                 {"id": "j2", "company": "Acme", "title": "Platform", "best_score": 70},
                 {"id": "j3", "company": "Beta", "title": "DevOps", "best_score": 80}]}


def test_enter_keeps_every_job():
    assert cli.approve_interactively(JOBS, input_fn=lambda prompt: "", say=lambda *a: None) == ["j1", "j2", "j3"]


def test_drop_removes_numbered_jobs():
    assert cli.approve_interactively(JOBS, input_fn=lambda prompt: "drop 2,3", say=lambda *a: None) == ["j1"]


def test_garbage_input_keeps_everything_rather_than_losing_work():
    assert cli.approve_interactively(JOBS, input_fn=lambda prompt: "huh", say=lambda *a: None) == ["j1", "j2", "j3"]


def test_parser_defaults_do_not_ask_so_the_report_comes_first():
    args = cli.parse_args([])
    assert (args.top, args.profile, args.refresh, args.ask, args.resume, args.url) == (5, "default", False, False,
                                                                                       None, None)


def test_url_flag_is_parsed():
    assert cli.parse_args(["--url", "https://x.test/1", "--skip-research"]).url == "https://x.test/1"
    assert cli.parse_args(["--skip-research"]).skip_research is True
