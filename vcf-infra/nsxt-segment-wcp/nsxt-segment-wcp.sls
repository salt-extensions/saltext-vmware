{% from "nsxt-segment-wcp/map.jinja" import cfg with context %}

{{ cfg.segment.name }}:
  vcf_nsx_segment.present:
    - transport_zone_path: {{ cfg.segment.spec.transport_zone_path }}
    - subnets: {{ cfg.segment.spec.subnets }}
    - advanced_config: {{ cfg.segment.spec.advanced_config }}
