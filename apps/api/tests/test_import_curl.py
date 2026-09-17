"""A pasted cURL command becomes one check, with its credentials as secrets (FR12)."""

from __future__ import annotations

import pytest
from nexovue_platform.checks.config import (
    ApiCheckConfig,
    BasicAuth,
    BearerAuth,
    FormBody,
    HttpRequest,
    JsonBody,
    MultipartBody,
    MultipartField,
    NoBody,
    TextBody,
    parse_config,
)

from nexovue_api.checkimports import curl
from nexovue_api.checkimports.schemas import ImportPlan
from nexovue_api.errors.exceptions import InvalidRequestError


def _request(plan: ImportPlan) -> HttpRequest:
    """The one request, after proving every config is one a save would accept."""
    assert len(plan.items) == 1
    stored = parse_config(plan.items[0].config.model_dump(mode="json"))
    assert isinstance(stored, ApiCheckConfig)
    assert stored.request.assertions, "every imported check must be able to fail (BR1)"
    return stored.request


def _skipped(plan: ImportPlan) -> dict[str, str]:
    return {skipped.item: skipped.reason for skipped in plan.skipped}


def _config_json(plan: ImportPlan) -> str:
    return "".join(item.config.model_dump_json() for item in plan.items)


# --- AC7 ----------------------------------------------------------------------------


def test_method_headers_json_body_and_bearer_token_are_read_with_the_token_as_a_secret() -> None:
    plan = curl.preview(
        "curl -X PATCH 'https://api.example.com/v1/orders/42' \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -H 'X-Request-Source: import-test' \\\n"
        "  -H 'Authorization: Bearer tok-7f3a9c' \\\n"
        '  -d \'{"status": "shipped"}\''
    )

    request = _request(plan)
    assert request.method == "PATCH"
    assert request.url == "https://api.example.com/v1/orders/42"
    assert [(h.name, h.value) for h in request.headers] == [
        ("Content-Type", "application/json"),
        ("X-Request-Source", "import-test"),
    ]
    assert request.body == JsonBody(content='{"status": "shipped"}')
    assert request.auth == BearerAuth(token_secret="API_EXAMPLE_COM_TOKEN")
    assert [(s.name, s.value) for s in plan.secrets] == [("API_EXAMPLE_COM_TOKEN", "tok-7f3a9c")]
    assert "tok-7f3a9c" not in _config_json(plan)


def test_the_check_is_named_for_its_method_host_and_path_and_asserts_no_error_status() -> None:
    plan = curl.preview("curl https://api.example.com/health?verbose=1")

    request = _request(plan)
    assert plan.items[0].name == "GET api.example.com/health"
    assert [(a.source, a.comparison, a.target) for a in request.assertions] == [
        ("status_code", "less_than", "400")
    ]


def test_a_long_name_is_cut_to_the_limit_a_save_enforces() -> None:
    plan = curl.preview(f"curl https://api.example.com/{'segment/' * 40}")

    assert len(plan.items[0].name) == 120


# --- AC8 and local files ------------------------------------------------------------


def test_a_form_file_is_shown_as_a_file_to_attach_and_left_out_of_the_body() -> None:
    plan = curl.preview(
        "curl -F 'file=@/home/me/report.csv' -F 'note=monthly' https://api.example.com/upload"
    )

    request = _request(plan)
    assert request.method == "POST"
    assert request.body == MultipartBody(parts=[MultipartField(name="note", value="monthly")])
    assert plan.items[0].needs_files == [
        "form field 'file' refers to a local file /home/me/report.csv — attach it"
    ]


def test_a_body_read_from_a_local_file_is_a_file_to_attach() -> None:
    plan = curl.preview("curl -d @payload.json https://api.example.com/orders")

    assert _request(plan).method == "POST"
    assert plan.items[0].needs_files == [
        "the request body is the local file payload.json — attach it"
    ]


def test_an_uploaded_file_is_a_put_with_a_file_to_attach() -> None:
    plan = curl.preview("curl -T backup.tar https://files.example.com/backup.tar")

    assert _request(plan).method == "PUT"
    assert plan.items[0].needs_files == [
        "the request body is the local file backup.tar — attach it"
    ]


# --- EC5 ----------------------------------------------------------------------------


def test_every_unsupported_option_is_listed_by_name_and_none_is_dropped_silently() -> None:
    plan = curl.preview(
        "curl -k --connect-timeout 5 --proxy http://proxy:3128 --http2 --frobnicate "
        "https://api.example.com/health"
    )

    skipped = _skipped(plan)
    assert set(skipped) == {
        "-k",
        "--connect-timeout 5",
        "--proxy http://proxy:3128",
        "--http2",
        "--frobnicate",
    }
    assert "certificate verification cannot be turned off" in skipped["-k"]
    assert _request(plan).url == "https://api.example.com/health"


def test_output_only_options_change_nothing_and_are_not_reported() -> None:
    plan = curl.preview("curl -sS -o out.txt -w '%{http_code}' --compressed https://example.com/")

    assert plan.skipped == []


def test_a_second_url_is_listed_rather_than_guessed_at() -> None:
    plan = curl.preview("curl https://one.example.com/ https://two.example.com/")

    assert _request(plan).url == "https://one.example.com/"
    assert _skipped(plan) == {"https://two.example.com/": "only one URL is imported from a command"}


# --- EC8: credentials become secrets ------------------------------------------------


def test_user_and_password_become_basic_auth_with_the_password_as_a_secret() -> None:
    plan = curl.preview("curl -u alice:hunter22 https://api.example.com/me")

    assert _request(plan).auth == BasicAuth(
        username="alice", password_secret="API_EXAMPLE_COM_PASSWORD"
    )
    assert [(s.name, s.value) for s in plan.secrets] == [("API_EXAMPLE_COM_PASSWORD", "hunter22")]
    assert "hunter22" not in _config_json(plan)


def test_a_user_without_a_password_asks_for_one() -> None:
    plan = curl.preview("curl -u alice https://api.example.com/me")

    assert isinstance(_request(plan).auth, BasicAuth)
    assert plan.items[0].needs_values == ["the password for user alice"]
    assert plan.secrets[0].value == ""


def test_a_basic_authorization_header_is_decoded_into_basic_auth() -> None:
    plan = curl.preview(
        "curl -H 'Authorization: Basic YWxpY2U6aHVudGVyMjI=' https://api.example.com/me"
    )

    request = _request(plan)
    assert request.auth == BasicAuth(username="alice", password_secret="API_EXAMPLE_COM_PASSWORD")
    assert request.headers == []
    assert plan.secrets[0].value == "hunter22"


def test_another_authorization_scheme_is_sent_from_a_secret() -> None:
    plan = curl.preview("curl -H 'Authorization: Token abc123def' https://api.example.com/me")

    request = _request(plan)
    assert [(h.name, h.value) for h in request.headers] == [
        ("Authorization", "{{secret.API_EXAMPLE_COM_AUTHORIZATION}}")
    ]
    assert plan.secrets[0].value == "Token abc123def"
    assert "abc123def" not in _config_json(plan)


@pytest.mark.parametrize("header", ["X-Api-Key", "api-key", "apikey", "X-Auth-Token", "Cookie"])
def test_a_credential_header_is_sent_from_a_secret(header: str) -> None:
    plan = curl.preview(f"curl -H '{header}: k-99887766' https://api.example.com/me")

    request = _request(plan)
    assert request.headers[0].value.startswith("{{secret.API_EXAMPLE_COM_")
    assert plan.secrets[0].value == "k-99887766"
    assert "k-99887766" not in _config_json(plan)


def test_credentials_in_the_url_become_basic_auth_and_leave_the_url() -> None:
    plan = curl.preview("curl https://bob:s%40fe@api.example.com/me")

    request = _request(plan)
    assert request.url == "https://api.example.com/me"
    assert request.auth == BasicAuth(username="bob", password_secret="API_EXAMPLE_COM_PASSWORD")
    assert plan.secrets[0].value == "s@fe"


def test_an_oauth2_bearer_option_becomes_bearer_auth() -> None:
    plan = curl.preview("curl --oauth2-bearer tok-1 https://api.example.com/me")

    assert _request(plan).auth == BearerAuth(token_secret="API_EXAMPLE_COM_TOKEN")


def test_two_different_credentials_for_one_host_get_two_secrets() -> None:
    plan = curl.preview(
        "curl -H 'Authorization: Bearer first' -H 'X-Api-Key: second' https://api.example.com/"
    )

    assert sorted(s.name for s in plan.secrets) == [
        "API_EXAMPLE_COM_TOKEN",
        "API_EXAMPLE_COM_X_API_KEY",
    ]


# --- methods and bodies -------------------------------------------------------------


def test_form_data_without_a_content_type_becomes_a_form_body() -> None:
    plan = curl.preview("curl -d 'name=Ada Lovelace' -d 'role=admin' https://api.example.com/users")

    request = _request(plan)
    assert request.method == "POST"
    assert isinstance(request.body, FormBody)
    assert [(f.name, f.value) for f in request.body.fields] == [
        ("name", "Ada Lovelace"),
        ("role", "admin"),
    ]


def test_json_looking_data_without_a_content_type_becomes_a_json_body() -> None:
    plan = curl.preview("""curl --data-raw '[1, 2]' https://api.example.com/items""")

    assert _request(plan).body == JsonBody(content="[1, 2]")


def test_other_data_is_sent_as_text_with_its_declared_type() -> None:
    plan = curl.preview(
        "curl -H 'Content-Type: text/csv' --data-binary 'a,b' https://api.example.com/rows"
    )

    assert _request(plan).body == TextBody(content="a,b", content_type="text/csv")


def test_the_json_option_sets_a_json_body_and_accepts_json() -> None:
    plan = curl.preview("""curl --json '{"a": 1}' https://api.example.com/items""")

    request = _request(plan)
    assert request.body == JsonBody(content='{"a": 1}')
    assert ("Accept", "application/json") in [(h.name, h.value) for h in request.headers]


def test_get_moves_the_data_into_the_query() -> None:
    plan = curl.preview(
        "curl -G -d 'q=open orders' --data-urlencode 'tag=a&b' https://api.example.com/search"
    )

    request = _request(plan)
    assert request.method == "GET"
    assert request.body == NoBody()
    assert [(q.name, q.value) for q in request.query] == [("q", "open orders"), ("tag", "a&b")]


def test_head_and_clustered_short_options_are_read() -> None:
    plan = curl.preview("curl -sIL -XHEAD -A probe/1.0 https://example.com/")

    request = _request(plan)
    assert request.method == "HEAD"
    assert request.follow_redirects is True
    assert [(h.name, h.value) for h in request.headers] == [("User-Agent", "probe/1.0")]


def test_redirects_are_not_followed_unless_the_command_says_so() -> None:
    assert _request(curl.preview("curl https://example.com/")).follow_redirects is False


def test_a_url_without_a_scheme_is_http_as_curl_assumes() -> None:
    assert _request(curl.preview("curl example.com/ping")).url == "http://example.com/ping"


def test_a_time_limit_is_kept_within_what_a_check_allows() -> None:
    plan = curl.preview("curl --max-time 90 https://example.com/")

    assert _request(plan).timeout_seconds == 30
    assert "--max-time 90" in _skipped(plan)


def test_an_invalid_header_name_is_listed_as_skipped() -> None:
    plan = curl.preview("curl -H 'Bad Header: x' https://example.com/")

    assert _request(plan).headers == []
    assert "not a valid header name" in _skipped(plan).values()


# --- refusals -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("wget https://example.com/", "starts with curl"),
        ("curl -X POST", "no URL"),
        ("curl 'https://example.com/", "Check its quotes"),
        ('curl ^\n  "https://example.com/" ^\n  -H "A: b"', "Windows"),
        ("curl -H", "needs a value"),
        ("curl -d a=1 -F b=2 https://example.com/", "mixes -d, --json and -F"),
        ("curl -X 'BAD METHOD' https://example.com/", "cannot become a check"),
    ],
)
def test_a_command_that_cannot_be_imported_is_refused_saying_why(
    command: str, message: str
) -> None:
    with pytest.raises(InvalidRequestError) as refused:
        curl.preview(command)

    assert message in refused.value.detail


def test_a_command_over_the_size_limit_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="longer than 64 KB"):
        curl.preview("curl https://example.com/ -d " + "a" * (64 * 1024))


def test_every_config_a_command_produces_is_one_a_save_accepts() -> None:
    plan = curl.preview(
        "curl -X POST -u u:p -F a=b -F f=@x.bin -H 'X-Api-Key: kkkk' https://example.com/"
    )

    _request(plan)
    assert isinstance(parse_config(plan.items[0].config.model_dump(mode="json")), ApiCheckConfig)
    assert MultipartField(name="a", value="b") in _multipart_parts(plan)


def _multipart_parts(plan: ImportPlan) -> list[object]:
    body = _request(plan).body
    assert isinstance(body, MultipartBody)
    return list(body.parts)
