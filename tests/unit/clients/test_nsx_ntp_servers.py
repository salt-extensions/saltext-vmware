"""Tests for the NSX Manager node NTP config client."""

import json

import responses

from saltext.vcf.clients import nsx_ntp_servers

URL = "https://nsx.test/api/v1/node/network/ntp-servers"


def test_ntp_get(opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        URL,
        json={"resource_type": "NtpServersConfig", "ntp_servers": ["10.0.0.1"]},
        status=200,
    )
    got = nsx_ntp_servers.ntp_get(opts)
    assert got["ntp_servers"] == ["10.0.0.1"]


def test_ntp_set(opts, mocked_responses):
    mocked_responses.add(responses.PUT, URL, json={"ntp_servers": ["10.0.0.2"]}, status=200)
    nsx_ntp_servers.ntp_set(opts, ["10.0.0.2"])
    sent = json.loads(mocked_responses.calls[0].request.body)
    assert sent == {"ntp_servers": ["10.0.0.2"]}
