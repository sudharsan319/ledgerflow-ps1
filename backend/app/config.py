"""Runtime configuration. Secrets never leave this module's server-side consumers."""
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    openrouter_api_key: str = field(default='', repr=False)
    openrouter_model: str = 'openai/gpt-4o-mini'
    enable_openrouter: bool = False
    max_pages: int = 20
    max_file_bytes: int = 10 * 1024 * 1024
    queue_capacity: int = 100
    public_origin: str = ''
    auth_username: str = 'reviewer'
    auth_password: str = field(default='', repr=False)

    def __post_init__(self):
        if self.public_origin:
            origin = urlsplit(self.public_origin)
            if (origin.scheme != 'https' or not origin.hostname or origin.username is not None or origin.password is not None
                    or origin.path not in ('', '/') or origin.query or origin.fragment):
                raise ValueError('LEDGERFLOW_PUBLIC_ORIGIN must be an HTTPS origin without a path or credentials.')
            if len(self.auth_password) < 20:
                raise ValueError('Hosted access requires LEDGERFLOW_AUTH_PASSWORD with at least 20 characters.')
        if self.auth_password and (not self.auth_username or ':' in self.auth_username or not self.auth_username.isascii()):
            raise ValueError('LEDGERFLOW_AUTH_USERNAME must be nonempty ASCII text without a colon.')

    @classmethod
    def from_env(cls):
        public_origin = os.getenv('LEDGERFLOW_PUBLIC_ORIGIN') or os.getenv('RENDER_EXTERNAL_URL') or ''
        if not public_origin and os.getenv('RENDER_EXTERNAL_HOSTNAME'):
            public_origin = 'https://' + os.environ['RENDER_EXTERNAL_HOSTNAME']
        if os.getenv('RENDER') == 'true' and not public_origin:
            raise ValueError('Render public address is unavailable; set LEDGERFLOW_PUBLIC_ORIGIN.')
        return cls(
            data_dir=Path(os.getenv('LEDGERFLOW_DATA_DIR', str(ROOT / 'data'))),
            openrouter_api_key=os.getenv('OPENROUTER_API_KEY', ''),
            openrouter_model=os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini'),
            enable_openrouter=os.getenv('ENABLE_OPENROUTER', 'false').lower() == 'true',
            public_origin=public_origin.rstrip('/'),
            auth_username=os.getenv('LEDGERFLOW_AUTH_USERNAME', 'reviewer'),
            auth_password=os.getenv('LEDGERFLOW_AUTH_PASSWORD', ''),
        )

    def extraction_settings(self):
        return {key: getattr(self, key) for key in ('openrouter_api_key', 'openrouter_model', 'enable_openrouter', 'max_pages')}
