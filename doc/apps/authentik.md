# Authentik
If you are using Authentik on a custom domain, to prevent CSRF error, you need to allow your hostname by configuring.
Create a file named `user_settings.py` with a content like the following one:
```
CSRF_TRUSTED_ORIGINS = ["https://authentik.mydomain.ltd", "https://*.mydomain.ltd"]
```

Store this file within the authentik config volume:
```
- ./authentik/user_settings.py:/data/user_settings.py
```

NB: In the provided configuration volume is in the local folder to prevent chown error on my personal FAT32 configration. Linux is not able to properly change the file ownership on FAT32 systems.

Each application you want to protect required a specifc entry in Authentik. A step by step guide can be found here: https://docs.ibracorp.io/authentik/authentik/docker-compose/traefik-forward-auth-single-applications
Once configured you just need to put `sso: true` on the config.yaml file and execute the run script to create the new configuration.