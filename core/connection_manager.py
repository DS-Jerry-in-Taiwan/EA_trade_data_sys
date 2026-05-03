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
        hosts = [self.settings['connection']['default_host']] + self.settings['connection']['fallback_hosts']
        port = self.settings['connection']['port']
        
        for host in hosts:
            print(f'>>> [TRY] Connecting to {host}:{port}...')
            try:
                mt5 = MetaTrader5(host=host, port=port)
                acc = self.get_active_account()
                if mt5.initialize(login=acc['login'], password=acc['password'], server=acc['server']):
                    print(f'>>> [SUCCESS] Connected to {host} using account {acc["login"]}')
                    return mt5
            except Exception as e:
                print(f'>>> [FAIL] Host {host} unreachable: {e}')
        return None
