import os
import yaml
import json
import socket
from pymt5linux import MetaTrader5


_CLOSED_MARKER = '_trade_data_connection_closed'


def close_mt5_connection(mt5):
    """Close both MT5 and the transport owned by pymt5linux.

    pymt5linux's ``shutdown()`` only invokes the remote MetaTrader5 shutdown;
    it does not close the private RPyC connection. Prefer a public ``close``
    method when the installed wrapper provides one, otherwise close the
    name-mangled transport used by current pymt5linux releases.
    """
    if mt5 is None:
        return

    # Mark first so cleanup remains idempotent even when either close operation
    # raises. MetaTrader5 is a local wrapper, so this does not invoke RPyC.
    try:
        local_state = object.__getattribute__(mt5, '__dict__')
        if local_state.get(_CLOSED_MARKER, False):
            return
        local_state[_CLOSED_MARKER] = True
    except Exception:
        pass

    shutdown = getattr(mt5, 'shutdown', None)
    if callable(shutdown):
        try:
            shutdown()
        except Exception:
            pass

    # Inspect the wrapper class first. Some RPC proxies synthesize arbitrary
    # instance attributes, so getattr(mt5, 'close') alone is not proof that a
    # public local transport close API exists.
    public_close = getattr(type(mt5), 'close', None)
    if callable(public_close):
        try:
            public_close(mt5)
        except Exception:
            pass
        return

    try:
        transport = object.__getattribute__(mt5, '_MetaTrader5__conn')
    except (AttributeError, TypeError):
        transport = None
    transport_close = getattr(transport, 'close', None)
    if callable(transport_close):
        try:
            transport_close()
        except Exception:
            pass


def configure_mt5_transport_timeout(mt5, timeout):
    """Override pymt5linux's 300s RPyC sync timeout on its real transport."""
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError('connection.timeout must be a positive number') from exc
    if timeout <= 0:
        raise ValueError('connection.timeout must be a positive number')

    try:
        transport = object.__getattribute__(mt5, '_MetaTrader5__conn')
        config = object.__getattribute__(transport, '_config')
    except (AttributeError, TypeError) as exc:
        raise ConnectionError('Unable to configure MT5 transport timeout') from exc
    config['sync_request_timeout'] = timeout


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

        connection = self.settings.get('connection', {})
        self.timeout = connection.get('timeout', 10)
        try:
            self.timeout = float(self.timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError('connection.timeout must be a positive number') from exc
        if self.timeout <= 0:
            raise ValueError('connection.timeout must be a positive number')
            
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
            configure_mt5_transport_timeout(mt5, self.timeout)
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
                close_mt5_connection(mt5)
