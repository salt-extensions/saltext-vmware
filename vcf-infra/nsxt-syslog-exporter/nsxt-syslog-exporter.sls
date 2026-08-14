{% from "nsxt-syslog-exporter/map.jinja" import cfg with context %}

{{ cfg.exporter.name }}:
  vcf_nsx_syslog_exporter.present:
    - server: {{ cfg.exporter.server }}
    - port: {{ cfg.exporter.port }}
    - protocol: {{ cfg.exporter.protocol }}
