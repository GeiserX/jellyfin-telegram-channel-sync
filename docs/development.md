# Development

<p>
  <a href="https://hub.docker.com/r/drumsergio/jellyfin-telegram-channel-sync"><img src="https://img.shields.io/docker/image-size/drumsergio/jellyfin-telegram-channel-sync/latest?style=flat-square&color=6B4C9A" alt="Docker Image Size"></a>
  <img src="https://img.shields.io/badge/python-3.13-0088CC?style=flat-square&logo=python&logoColor=white" alt="Python 3.13">
  <a href="https://codecov.io/gh/GeiserX/jellyfin-telegram-channel-sync"><img src="https://codecov.io/gh/GeiserX/jellyfin-telegram-channel-sync/graph/badge.svg" alt="codecov"></a>
</p>

```bash
pip install -r app/requirements.txt && pip install pytest pytest-cov
pytest --cov=app
```

The tests need no environment variables and no network. [app/config.py](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/app/config.py) parses the environment inside a function, [app/sync.py](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/app/sync.py) decides over plain data, and fakes stand in for Jellyfin and Telegram.

