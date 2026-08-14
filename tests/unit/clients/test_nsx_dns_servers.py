"""Tests for the NSX Manager node DNS resolver client."""

import json

import responses

from saltext.vcf.clients import nsx_dns_servers

URL = "https://nsx.test/api/v1/node/network/name-servers"


def test_dns_get(opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        URL,
        json={"resource_type": "NameServersConfig", "name_servers": ["10.0.0.1"]},
        status=200,
    )
    got = nsx_dns_servers.dns_get(opts)
    assert got["name_servers"] == ["10.0.0.1"]


def test_dns_set(opts, mocked_responses):
    mocked_responses.add(responses.PUT, URL, json={"name_servers": ["10.0.0.2"]}, status=200)
    nsx_dns_servers.dns_set(opts, ["10.0.0.2"])
    sent = json.loads(mocked_responses.calls[0].request.body)
    assert sent == {"name_servers": ["10.0.0.2"]}
