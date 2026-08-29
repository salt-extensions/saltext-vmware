"""Tests for clients.ovf_deploy (pyVmomi OVA deploy engine)."""

import io
import tarfile
from unittest import mock

import pytest
from pyVmomi import vim

from saltext.vcf.clients import ovf_deploy

# ---------------------------------------------------------------------------
# Tarball construction helper
# ---------------------------------------------------------------------------


def _write_test_ova(path, *, ovf_text='<?xml version="1.0"?><Envelope/>', vmdk_bytes=b"VMDK"):
    with tarfile.open(path, "w") as tar:
        for name, data in [("test.ovf", ovf_text.encode()), ("test-disk1.vmdk", vmdk_bytes)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


# ---------------------------------------------------------------------------
# _materialize_ova
# ---------------------------------------------------------------------------


def test_materialize_local_path_returns_no_cleanup(tmp_path):
    ova = tmp_path / "x.ova"
    _write_test_ova(ova)
    path, cleanup = ovf_deploy._materialize_ova(str(ova), verify_ssl=False)
    assert path == str(ova)
    assert cleanup is None


def test_materialize_url_streams_to_tempfile(monkeypatch):
    class FakeResp:
        def __init__(self):
            self._data = [b"abc", b"def"]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield from self._data

    monkeypatch.setattr(ovf_deploy.requests, "get", lambda *a, **k: FakeResp())
    path, cleanup = ovf_deploy._materialize_ova("https://example/x.ova", verify_ssl=False)
    try:
        assert path == cleanup
        with open(path, "rb") as fp:
            assert fp.read() == b"abcdef"
    finally:
        ovf_deploy.Path(cleanup).unlink(missing_ok=True)


def test_materialize_missing_local_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ovf_deploy._materialize_ova(str(tmp_path / "nope.ova"), verify_ssl=False)


# ---------------------------------------------------------------------------
# _read_ovf_descriptor + _resolve_member
# ---------------------------------------------------------------------------


def test_read_ovf_descriptor(tmp_path):
    ova = tmp_path / "x.ova"
    _write_test_ova(ova, ovf_text="<Envelope>hi</Envelope>")
    with tarfile.open(ova) as tar:
        members = list(tar.getmembers())
        assert ovf_deploy._read_ovf_descriptor(tar, members) == "<Envelope>hi</Envelope>"


def test_read_ovf_descriptor_raises_when_missing(tmp_path):
    ova = tmp_path / "x.tar"
    with tarfile.open(ova, "w") as tar:
        info = tarfile.TarInfo("just-a-vmdk")
        info.size = 0
        tar.addfile(info, io.BytesIO(b""))
    with tarfile.open(ova) as tar:
        with pytest.raises(RuntimeError, match="no .ovf descriptor"):
            ovf_deploy._read_ovf_descriptor(tar, list(tar.getmembers()))


def test_resolve_member_exact_match():
    members = {"a/b/disk.vmdk": "MEMBER"}
    assert ovf_deploy._resolve_member(members, "a/b/disk.vmdk") == "MEMBER"


def test_resolve_member_basename_fallback():
    members = {"prefix/test-disk1.vmdk": "M"}
    assert ovf_deploy._resolve_member(members, "test-disk1.vmdk") == "M"


def test_resolve_member_raises():
    with pytest.raises(LookupError):
        ovf_deploy._resolve_member({"other.vmdk": "X"}, "missing.vmdk")


# ---------------------------------------------------------------------------
# _CountingReader
# ---------------------------------------------------------------------------


def test_counting_reader_updates_progress():
    progress = mock.MagicMock()
    src = io.BytesIO(b"x" * 1000)
    r = ovf_deploy._CountingReader(src, prior_sent=500, total=2000, progress=progress)
    # read in two chunks; reader uses _CHUNK if -1
    assert len(r.read(400)) == 400
    progress.set_percent.assert_called_with(100 * (500 + 400) / 2000)
    r.read(600)
    progress.set_percent.assert_called_with(100 * (500 + 1000) / 2000)
    # exhausted
    assert r.read(100) == b""


# ---------------------------------------------------------------------------
# _create_import_spec error handling
# ---------------------------------------------------------------------------


def test_create_import_spec_raises_on_error():
    content = mock.MagicMock()
    err = mock.MagicMock()
    err.msg = "bad spec"
    content.ovfManager.CreateImportSpec.return_value = mock.MagicMock(
        error=[err], warning=[], importSpec=None, fileItem=[]
    )
    with pytest.raises(RuntimeError, match="bad spec"):
        ovf_deploy._create_import_spec(
            content=content,
            ovf_xml="<x/>",
            vm_name="vm",
            rp=mock.MagicMock(),
            ds=mock.MagicMock(),
            network_refs={},
            ovf_properties={},
            disk_provisioning="thin",
            deployment_option=None,
            host_ref=None,
        )


def test_create_import_spec_returns_spec_and_files():
    content = mock.MagicMock()
    fi = mock.MagicMock(deviceId="dev-1", path="d1.vmdk", size=100, create=True)
    content.ovfManager.CreateImportSpec.return_value = mock.MagicMock(
        error=[], warning=[], importSpec="IMPORT_SPEC", fileItem=[fi]
    )
    spec, files = ovf_deploy._create_import_spec(
        content=content,
        ovf_xml="<x/>",
        vm_name="vm",
        rp=mock.MagicMock(),
        ds=mock.MagicMock(),
        network_refs={"OvfNet": mock.MagicMock(spec=vim.Network)},
        ovf_properties={"prop1": "v"},
        disk_provisioning="thin",
        deployment_option="small",
        host_ref=mock.MagicMock(spec=vim.HostSystem),
    )
    assert spec == "IMPORT_SPEC"
    assert files == [fi]
    # Verify the cisp argument carried our inputs
    _, kwargs = content.ovfManager.CreateImportSpec.call_args
    cisp = kwargs["cisp"]
    assert cisp.entityName == "vm"
    assert cisp.diskProvisioning == "thin"
    assert cisp.deploymentOption == "small"
    assert len(cisp.networkMapping) == 1
    assert cisp.networkMapping[0].name == "OvfNet"
    assert len(cisp.propertyMapping) == 1
    assert cisp.propertyMapping[0].key == "prop1"


# ---------------------------------------------------------------------------
# _wait_lease_ready
# ---------------------------------------------------------------------------


def test_wait_lease_ready_returns_immediately_when_ready(monkeypatch):
    monkeypatch.setattr(ovf_deploy.time, "sleep", lambda _s: None)
    lease = mock.MagicMock(state=vim.HttpNfcLease.State.ready)
    ovf_deploy._wait_lease_ready(lease, timeout=1)


def test_wait_lease_ready_raises_on_error(monkeypatch):
    monkeypatch.setattr(ovf_deploy.time, "sleep", lambda _s: None)
    err = mock.MagicMock()
    err.msg = "lease died"
    lease = mock.MagicMock(state=vim.HttpNfcLease.State.error, error=err)
    with pytest.raises(RuntimeError, match="lease died"):
        ovf_deploy._wait_lease_ready(lease, timeout=1)


def test_wait_lease_ready_times_out(monkeypatch):
    monkeypatch.setattr(ovf_deploy.time, "sleep", lambda _s: None)
    seq = iter([0, 0, 100])
    monkeypatch.setattr(ovf_deploy.time, "monotonic", lambda: next(seq))
    lease = mock.MagicMock(state=vim.HttpNfcLease.State.initializing)
    with pytest.raises(TimeoutError):
        ovf_deploy._wait_lease_ready(lease, timeout=60)


# ---------------------------------------------------------------------------
# _upload_disks
# ---------------------------------------------------------------------------


def test_upload_disks_puts_each_file_with_url_substitution(monkeypatch, tmp_path):
    ova = tmp_path / "x.ova"
    _write_test_ova(ova, vmdk_bytes=b"X" * 256)

    fi = mock.MagicMock(deviceId="key-1", path="test-disk1.vmdk", size=256, create=True)
    du = mock.MagicMock(importKey="key-1", key="key-1", url="https://*/nfc/abc/disk")

    calls = []

    def fake_put(url, data, headers, verify, timeout):
        # drain the streaming reader so progress is recorded
        if hasattr(data, "read"):
            while data.read(64):
                pass
        calls.append({"url": url, "headers": dict(headers)})
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        return resp

    monkeypatch.setattr(ovf_deploy.requests, "put", fake_put)

    with tarfile.open(ova) as tar:
        members = list(tar.getmembers())
        progress = mock.MagicMock()
        ovf_deploy._upload_disks(
            target_host="esx-1.lab",
            device_urls=[du],
            file_items=[fi],
            tar=tar,
            members=members,
            progress=progress,
            verify_ssl=False,
            timeout=60,
            session_cookie="vmware_soap_session=abc123",
        )
    assert len(calls) == 1
    assert calls[0]["url"] == "https://esx-1.lab/nfc/abc/disk"
    assert calls[0]["headers"]["Content-Type"] == "application/x-vnd.vmware-streamVmdk"
    assert calls[0]["headers"]["Content-Length"] == "256"
    assert calls[0]["headers"]["Cookie"] == "vmware_soap_session=abc123"
    assert calls[0]["headers"]["Overwrite"] == "t"


def test_upload_disks_raises_when_no_device_url_match(tmp_path):
    ova = tmp_path / "x.ova"
    _write_test_ova(ova)
    fi = mock.MagicMock(deviceId="unmatched", path="test-disk1.vmdk", size=4, create=True)
    du = mock.MagicMock(importKey="other", key="other", url="https://*/nfc")
    with tarfile.open(ova) as tar:
        with pytest.raises(RuntimeError, match="no device URL"):
            ovf_deploy._upload_disks(
                target_host="h",
                device_urls=[du],
                file_items=[fi],
                tar=tar,
                members=list(tar.getmembers()),
                progress=mock.MagicMock(),
                verify_ssl=False,
                timeout=10,
                session_cookie="vmware_soap_session=z",
            )


# ---------------------------------------------------------------------------
# _resolve_targets
# ---------------------------------------------------------------------------


def _fake_content(*, datastores, networks):
    """Build a fake content tree with one datacenter / one host / N datastores+networks."""
    host = mock.MagicMock(spec=vim.HostSystem)
    host.name = "esx-1"
    host.datastore = [mock.MagicMock(name=ds) for ds in datastores]
    for ds, m in zip(datastores, host.datastore):
        m.name = ds  # spec=mock argname doesn't override .name
    host.network = [mock.MagicMock(spec=vim.Network) for _ in networks]
    for net, m in zip(networks, host.network):
        m.name = net

    cr = mock.MagicMock(spec=vim.ComputeResource)
    cr.host = [host]
    cr.resourcePool = mock.MagicMock()

    dc = mock.MagicMock(spec=vim.Datacenter)
    dc.name = "ha-datacenter"
    dc.hostFolder.childEntity = [cr]
    dc.vmFolder = mock.MagicMock()

    content = mock.MagicMock()
    content.rootFolder.childEntity = [dc]
    return content, dc, cr, host


def test_resolve_targets_picks_first_when_no_datastore_specified():
    content, _dc, cr, host = _fake_content(datastores=["ds-a", "ds-b"], networks=["VM Network"])
    rp, _vm_folder, host_ref, ds_ref, nets = ovf_deploy._resolve_targets(
        content, datastore=None, network_map=None
    )
    assert rp is cr.resourcePool
    assert host_ref is host
    assert ds_ref.name == "ds-a"
    assert nets == {}


def test_resolve_targets_matches_named_datastore():
    content, _dc, _cr, _host = _fake_content(datastores=["ds-a", "ds-b"], networks=["VM Network"])
    _rp, _f, _h, ds_ref, _n = ovf_deploy._resolve_targets(
        content, datastore="ds-b", network_map=None
    )
    assert ds_ref.name == "ds-b"


def test_resolve_targets_raises_on_missing_datastore():
    content, _dc, _cr, _host = _fake_content(datastores=["ds-a"], networks=["VM Network"])
    with pytest.raises(LookupError, match="datastore 'missing-ds' not found"):
        ovf_deploy._resolve_targets(content, datastore="missing-ds", network_map=None)


def test_resolve_targets_maps_named_networks():
    content, _dc, _cr, _host = _fake_content(datastores=["ds-a"], networks=["mgmt-net", "vm-net"])
    _rp, _f, _h, _ds, nets = ovf_deploy._resolve_targets(
        content, datastore=None, network_map={"OvfMgmt": "mgmt-net"}
    )
    assert list(nets) == ["OvfMgmt"]
    assert nets["OvfMgmt"].name == "mgmt-net"


def test_resolve_targets_raises_on_missing_network():
    content, _dc, _cr, _host = _fake_content(datastores=["ds-a"], networks=["one-net"])
    with pytest.raises(LookupError, match="network 'no-net' not on host"):
        ovf_deploy._resolve_targets(content, datastore=None, network_map={"Ovf": "no-net"})


# ---------------------------------------------------------------------------
# deploy_ova — full happy path with heavy mocking
# ---------------------------------------------------------------------------


def test_deploy_ova_happy_path(monkeypatch, tmp_path):
    ova = tmp_path / "installer.ova"
    _write_test_ova(ova, vmdk_bytes=b"X" * 128)

    content, _dc, cr, _host = _fake_content(datastores=["datastore1"], networks=["VM Network"])
    si = mock.MagicMock()
    si.RetrieveContent.return_value = content
    si._stub.cookie = 'vmware_soap_session="abc123"; Path=/; HttpOnly'

    monkeypatch.setattr(ovf_deploy, "SmartConnect", lambda **_k: si)
    monkeypatch.setattr(ovf_deploy, "Disconnect", lambda _si: None)

    file_item = mock.MagicMock(deviceId="dev-1", path="test-disk1.vmdk", size=128, create=True)
    content.ovfManager.CreateImportSpec.return_value = mock.MagicMock(
        error=[], warning=[], importSpec="IS", fileItem=[file_item]
    )

    new_vm = mock.MagicMock()
    new_vm.name = "vcf-installer"
    new_vm._moId = "vm-100"
    new_vm.PowerOnVM_Task.return_value = mock.MagicMock()

    lease = mock.MagicMock()
    lease.state = vim.HttpNfcLease.State.ready
    lease.info.entity = new_vm
    device_url = mock.MagicMock(importKey="dev-1", key="dev-1", url="https://*/nfc/abc/disk")
    lease.info.deviceUrl = [device_url]
    cr.resourcePool.ImportVApp.return_value = lease

    monkeypatch.setattr(ovf_deploy, "wait_for_task", lambda _t: None)
    monkeypatch.setattr(ovf_deploy.time, "sleep", lambda _s: None)

    put_calls = []

    def fake_put(url, data, headers, verify, timeout):
        if hasattr(data, "read"):
            while data.read(64):
                pass
        put_calls.append({"url": url})
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        return resp

    monkeypatch.setattr(ovf_deploy.requests, "put", fake_put)

    # Stub _LeaseProgress so we don't spawn a real thread.
    class _NoopProgress:
        def __init__(self, _lease):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def set_percent(self, _pct):
            pass

    monkeypatch.setattr(ovf_deploy, "_LeaseProgress", _NoopProgress)

    result = ovf_deploy.deploy_ova(
        ova_source=str(ova),
        target_host="esx-1.lab",
        target_user="root",
        target_password="secret",
        vm_name="vcf-installer",
        network_map={"VM Network": "VM Network"},
    )
    assert result["vm_name"] == "vcf-installer"
    assert result["vm_moid"] == "vm-100"
    assert result["powered_on"] is True
    assert "elapsed_sec" in result
    assert put_calls == [{"url": "https://esx-1.lab/nfc/abc/disk"}]
    lease.HttpNfcLeaseComplete.assert_called_once()


# ---------------------------------------------------------------------------
# _sanitize_ovf_descriptor
# ---------------------------------------------------------------------------


_OVF_WITH_STORAGE_GROUP = """<?xml version="1.0"?>
<Envelope>
  <References>
    <File ovf:id="file1" ovf:href="disk1.vmdk"/>
  </References>
  <vmw:StorageGroupSection ovf:required="false" vmw:id="group1" vmw:name="lvn-cm2w3-vc1-c1-t0compute">
  </vmw:StorageGroupSection>
  <DiskSection>
    <Disk ovf:capacity="1024" ovf:capacityAllocationUnits="byte * 2^30" ovf:diskId="d1" ovf:fileRef="file1" ovf:format="streamOptimized" ovf:populatedSize="12530614272"/>
  </DiskSection>
  <VirtualSystem>
    <vmw:StorageSection ovf:required="false" vmw:group="group1"/>
  </VirtualSystem>
</Envelope>"""


def test_sanitize_strips_storage_group_and_section_by_default():
    result = ovf_deploy._sanitize_ovf_descriptor(_OVF_WITH_STORAGE_GROUP)
    assert "StorageGroupSection" not in result
    assert "StorageSection" not in result
    # Disk element preserved; capacity untouched with no clamp requested.
    assert 'ovf:capacity="1024"' in result


def test_sanitize_no_op_when_ovf_has_no_pod_storage_refs():
    plain = '<?xml version="1.0"?><Envelope><Disk ovf:capacity="20"/></Envelope>'
    result = ovf_deploy._sanitize_ovf_descriptor(plain)
    assert result == plain


def test_sanitize_clamps_oversize_disk_capacity_when_requested():
    result = ovf_deploy._sanitize_ovf_descriptor(_OVF_WITH_STORAGE_GROUP, max_disk_gib=128)
    assert 'ovf:capacity="128"' in result
    assert 'ovf:capacity="1024"' not in result


def test_sanitize_leaves_disks_smaller_than_clamp_alone():
    xml = '<Envelope><Disk ovf:capacity="32" ovf:diskId="d1"/></Envelope>'
    result = ovf_deploy._sanitize_ovf_descriptor(xml, max_disk_gib=128)
    assert 'ovf:capacity="32"' in result


# ---------------------------------------------------------------------------
# _apply_storage_profile
# ---------------------------------------------------------------------------


def test_apply_storage_profile_attaches_to_vm_and_all_disks():
    """Policy attach walks both configSpec.vmProfile and every VirtualDisk device."""
    disk_a = mock.Mock(spec=vim.vm.device.VirtualDeviceSpec)
    disk_a.device = mock.Mock(spec=vim.vm.device.VirtualDisk)
    disk_b = mock.Mock(spec=vim.vm.device.VirtualDeviceSpec)
    disk_b.device = mock.Mock(spec=vim.vm.device.VirtualDisk)
    nic = mock.Mock(spec=vim.vm.device.VirtualDeviceSpec)
    nic.device = mock.Mock(spec=vim.vm.device.VirtualEthernetCard)

    config_spec = mock.Mock()
    config_spec.deviceChange = [disk_a, disk_b, nic]
    import_spec = mock.Mock()
    import_spec.configSpec = config_spec

    ovf_deploy._apply_storage_profile(import_spec, "policy-id-1234")

    # VM-level: exactly one DefinedProfileSpec pointing at our id.
    assert len(config_spec.vmProfile) == 1
    assert config_spec.vmProfile[0].profileId == "policy-id-1234"
    # Per-disk: each VirtualDisk got its .profile set. NIC untouched.
    assert disk_a.profile[0].profileId == "policy-id-1234"
    assert disk_b.profile[0].profileId == "policy-id-1234"
    assert not hasattr(nic, "profile") or nic.profile != disk_a.profile  # NIC has no assignment


# ---------------------------------------------------------------------------
# find_vm
# ---------------------------------------------------------------------------


def test_find_vm_returns_none_when_missing(monkeypatch):
    def _fake_connect(*a, **kw):
        content = mock.Mock()
        content.rootFolder.childEntity = []
        si = mock.Mock()
        si.RetrieveContent.return_value = content
        return si

    monkeypatch.setattr(ovf_deploy, "_connect", _fake_connect)
    monkeypatch.setattr(ovf_deploy, "Disconnect", lambda si: None)

    result = ovf_deploy.find_vm(
        target_host="vc.test",
        target_user="u",
        target_password="p",
        vm_name="doesnt-exist",
    )
    assert result is None


def test_find_vm_returns_dict_when_present(monkeypatch):
    vm = mock.Mock(spec=vim.VirtualMachine)
    vm.name = "vrli"
    vm._moId = "vm-1042"
    vm.runtime.powerState = vim.VirtualMachinePowerState.poweredOn

    dc = mock.Mock()
    dc.vmFolder = mock.Mock()
    dc.vmFolder.childEntity = [vm]

    content = mock.Mock()
    content.rootFolder.childEntity = [dc]
    si = mock.Mock()
    si.RetrieveContent.return_value = content

    monkeypatch.setattr(ovf_deploy, "_connect", lambda *a, **kw: si)
    monkeypatch.setattr(ovf_deploy, "Disconnect", lambda si: None)

    result = ovf_deploy.find_vm(
        target_host="vc.test",
        target_user="u",
        target_password="p",
        vm_name="vrli",
    )
    assert result == {"vm_name": "vrli", "vm_moid": "vm-1042", "powered_on": True}
