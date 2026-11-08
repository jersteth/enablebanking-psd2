"""Building the signed API client from a Config; shared by the CLI and the Python API."""

from enablebanking_psd2 import __version__
from enablebanking_psd2.config import Config
from enablebanking_psd2.eb.auth import JwtSigner
from enablebanking_psd2.eb.client import EnableBankingClient
from enablebanking_psd2.errors import ConfigError
from enablebanking_psd2.log import http_event_hooks


def load_signer(cfg: Config) -> JwtSigner:
    try:
        return JwtSigner(cfg.app_id, cfg.key_path.read_bytes())
    except Exception as exc:  # bad PEM, encrypted key, unsupported key type
        raise ConfigError([f"private key {cfg.key_path} cannot be loaded: {exc}"]) from exc


def make_client(cfg: Config, signer: JwtSigner, *, retry_rate_limits: bool = True) -> EnableBankingClient:
    psu = {"Psu-User-Agent": f"psd2-demo/{__version__}"}
    if cfg.psu_ip_address:
        psu["Psu-Ip-Address"] = cfg.psu_ip_address
    return EnableBankingClient(signer, psu_headers=psu, event_hooks=http_event_hooks(),
                               retry_rate_limits=retry_rate_limits)
