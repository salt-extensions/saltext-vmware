{% from "nsxt-vidm/map.jinja" import cfg with context %}

nsxt-vidm:
  vcf_nsx_vidm.enabled:
    - vidm_enable: {{ cfg.vidm.enable }}
    - vidm_hostname: {{ cfg.vidm.hostname }}
    - vidm_domain: {{ cfg.vidm.domain_name }}
    - client_id: {{ cfg.vidm.client_id }}
    - client_secret: {{ cfg.vidm.client_secret }}
