"""NSX Manager node's own syslog exporters (``/api/v1/node/services/syslog/exporters``).

Forwards NSX Manager's own logs to external syslog collectors — distinct
from workload-facing logging (there is no equivalent at the NSX Policy
layer).

Field names below follow the standard NSX-T Node API shape but haven't
been exercised against a live NSX Manager — verify before real use.
"""

import requests

from saltext.vcf.utils import nsx

PATH = "/api/v1/node/services/syslog/exporters"


def list_(opts, profile=None):
    return nsx.api_get(opts, PATH, profile=profile)


def get(opts, exporter_name, profile=None):
    return nsx.api_get(opts, f"{PATH}/{exporter_name}", profile=profile)


def get_or_none(opts, exporter_name, profile=None):
    try:
        return get(opts, exporter_name, profile=profile)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def create(opts, exporter_name, server, port, protocol, profile=None, **spec):
    """Create a syslog exporter.

    *protocol* is one of ``TCP``, ``UDP``, ``TLS``, ``LI``, ``LI-TLS``.
    Extra fields (``level``, ``msgid``, ``facility``, ...) pass through
    via *spec*.
    """
    body = {
        "exporter_name": exporter_name,
        "server": server,
        "port": int(port),
        "protocol": protocol,
    }
    body.update(spec)
    return nsx.api_post(opts, PATH, body=body, profile=profile)


def delete(opts, exporter_name, profile=None):
    return nsx.api_delete(opts, f"{PATH}/{exporter_name}", profile=profile)
