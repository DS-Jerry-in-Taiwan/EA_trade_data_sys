import os
import yaml
import json
import socket
from pymt5linux import MetaTrader5

class MT5Connector:
    def __init__(self, settings_path=None, accounts_path=None):
        settings_path = settings_path or os.getenv('MT5_SETTINGS_PATH', '/app/service/config/settings.yaml')
        accounts_path = accounts_path or os.getenv('MT5_ACCOUNTS_PATH', '/app/service/config/accounts.json')
        
        if not os.path.exists(settings_path):
            raise FileNotFoundError(f"Settings file not found: {settings_path}")
        if not os.path.exists(accounts_path):
            raise FileNotFoundError(f"Accounts file not found: {accounts_path}")
        
        with open(settings_path, 'r') as f:
            self.settings = yaml.safe_load(f)
        with open(accounts_path, 'r') as f:
            self.accounts = json.load(f)
            
    def get_active_account(self):
        active = self.accounts['active_provider']
        return self.accounts['providers'][active]

    def connect(self):
        connection = self.settings['connection']
        host = connection['default_host']
        port = connection['port']

        try:
            socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ConnectionError(
                f"Unable to resolve MT5 host {host}:{port}"
            ) from exc

        mt5 = None
        initialized = False
        try:
            mt5 = MetaTrader5(host=host, port=port)
            account = self.get_active_account()
            initialized = mt5.initialize(
                login=account['login'],
                password=account['password'],
                server=account['server'],
            )
            if not initialized:
                raise ConnectionError(
                    f"MT5 initialization failed for {host}:{port}"
                )
            return mt5
        except ConnectionError:
            raise
        except Exception as exc:
            raise ConnectionError(
                f"Unable to connect to MT5 at {host}:{port}"
            ) from exc
        finally:
            if mt5 is not None and not initialized:
                try:
                    mt5.shutdown()
                except Exception:
                    pass
