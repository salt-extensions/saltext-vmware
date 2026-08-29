{% from "nsxt-localos-user/map.jinja" import cfg with context %}

{{ cfg.user.username }}:
  vcf_nsx_localos_user.present:
    - password: {{ cfg.user.password }}
    - role: {{ cfg.user.role }}
