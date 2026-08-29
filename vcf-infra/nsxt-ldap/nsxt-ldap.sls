{% from "nsxt-ldap/map.jinja" import cfg with context %}

{{ cfg.provider.name }}:
  vcf_nsx_ldap.present:
    - ldap_servers: {{ cfg.provider.ldap_servers }}
    - base_dn: {{ cfg.provider.base_dn }}
    - domain_name: {{ cfg.provider.domain_name }}
