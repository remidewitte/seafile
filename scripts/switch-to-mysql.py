#!/usr/bin/env python
# coding: UTF-8

'''This is the helper script to update seafile server from sqlite3 to mysql'''

import sys

####################
### Requires Python 2.6+
####################
if sys.version_info.major == 3:
    print 'Python 3 not supported yet. Quit now'
    sys.exit(1)
if sys.version_info.minor < 6:
    print 'Python 2.6 or above is required. Quit now'
    sys.exit(1)

import os
import time
import re
import shutil
import subprocess
import sqlite3
import hashlib
import getpass
import optparse
import warnings

from contextlib import contextmanager
from ConfigParser import ConfigParser

try:
    import MySQLdb
except ImportError:
    err = '''\
Please install python MySQLdb library first.

For Debian/Ubuntu:
    sudo apt-get install python-mysqldb

For CentOS/RHEL:
    sudo yum install MYSQL-python
'''
    sys.stderr.write(err)
    sys.exit(1)

try:
    import readline
    # Avoid pylint 'unused import' warning
    dummy = readline
except ImportError:
    pass

####################
### Cosntants
####################
SERVER_MANUAL_HTTP = 'https://github.com/haiwen/seafile/wiki'
SEAFILE_GOOGLE_GROUP = 'https://groups.google.com/forum/?fromgroups#!forum/seafile'
SEAFILE_WEBSITE = 'http://www.seafile.com'
SEAHUB_DOWNLOAD_URL = 'https://seafile.com.cn/downloads/seahub-latest.tar.gz'

####################
### Global variables
####################
conf = {}

CONF_TOP_DIR = 'topdir'
CONF_INSTALL_PATH = 'installpath'
CONF_CCNET_DIR = 'ccnet_dir'
CONF_SEAFILE_DIR = 'seafile_dir'
CONF_SEAHUB_DIR = 'seahub_dir'

CONF_RESET_ADMIN = 'reset_admin'
CONF_ADMIN_EMAIL = 'admin_email'
CONF_ADMIN_PASSWORD = 'admin_password'

CONF_USE_EXISTING_DB = 'use_existing_db'
CONF_MYSQL_ROOT_PASSWORD = 'root_password'
CONF_MYSQL_HOST = 'mysql_host'
CONF_MYSQL_SEAFILE_USER = 'seafile_user'
CONF_MYSQL_SEAFILE_PASSWORD = 'seafile_password'

CONF_DB_NAME_CCNET = 'db_name_ccnet'
CONF_DB_NAME_SEAFILE = 'db_name_seafile'
CONF_DB_NAME_SEAHUB = 'db_name_seahub'

CONF_MYSQL_ROOT_CONN = 'conn'

####################
### Common helper functions
####################

def welcome():
    '''Show welcome message when running the <setup> command'''
    welcome_msg = '''\
-----------------------------------------------------------------
This script will guide you to switch your seafile server to use mysql.
Make sure you have read seafile server manual at

        %s

Press [ENTER] to continue
-----------------------------------------------------------------
''' % SERVER_MANUAL_HTTP
    print welcome_msg
    raw_input()

def highlight(content):
    '''Add ANSI color to content to get it highlighted on terminal'''
    return '\x1b[33m%s\x1b[m' % content

def info(msg):
    print msg

def usage(usage):
    print usage
    sys.exit(1)

def error(msg):
    print 'Error: ' + msg
    sys.exit(1)

def run_argv(argv, cwd=None, env=None, suppress_stdout=False, suppress_stderr=False):
    '''Run a program and wait it to finish, and return its exit code. The
    standard output of this program is supressed.

    '''
    with open(os.devnull, 'w') as devnull:
        if suppress_stdout:
            stdout = devnull
        else:
            stdout = sys.stdout

        if suppress_stderr:
            stderr = devnull
        else:
            stderr = sys.stderr

        proc = subprocess.Popen(argv,
                                cwd=cwd,
                                stdout=stdout,
                                stderr=stderr,
                                env=env)
        return proc.wait()

def run(cmdline, cwd=None, env=None, suppress_stdout=False, suppress_stderr=False):
    '''Like run_argv but specify a command line string instead of argv'''
    with open(os.devnull, 'w') as devnull:
        if suppress_stdout:
            stdout = devnull
        else:
            stdout = sys.stdout

        if suppress_stderr:
            stderr = devnull
        else:
            stderr = sys.stderr

        proc = subprocess.Popen(cmdline,
                                cwd=cwd,
                                stdout=stdout,
                                stderr=stderr,
                                env=env,
                                shell=True)
        return proc.wait()

def is_running(process):
    '''Detect if there is a process with the given name running'''
    argv = [
        'pgrep', '-f', process
    ]

    return run_argv(argv, suppress_stdout=True) == 0

def prepend_env_value(name, value, seperator=':'):
    '''append a new value to a list'''
    try:
        current_value = os.environ[name]
    except KeyError:
        current_value = ''

    new_value = value
    if current_value:
        new_value += seperator + current_value

    os.environ[name] = new_value

def must_mkdir(path):
    '''Create a directory, exit on failure'''
    try:
        os.mkdir(path)
    except OSError, e:
        error('failed to create directory %s:%s' % (path, e))


def find_in_path(prog):
    if 'win32' in sys.platform:
        sep = ';'
    else:
        sep = ':'

    dirs = os.environ['PATH'].split(sep)
    for d in dirs:
        d = d.strip()
        if d == '':
            continue
        path = os.path.join(d, prog)
        if os.path.exists(path):
            return path

    return None

def _get_python_executable():
    if sys.executable and os.path.isabs(sys.executable) and os.path.exists(sys.executable):
        return sys.executable

    try_list = [
        'python2.7',
        'python27',
        'python2.6',
        'python26',
    ]

    for prog in try_list:
        path = find_in_path(prog)
        if path is not None:
            return path

    path = os.environ.get('PYTHON', 'python')

    return path

pyexec = None
def get_python_executable():
    '''Find a suitable python executable'''
    global pyexec
    if pyexec is not None:
        return pyexec

    pyexec = _get_python_executable()
    return pyexec

def validate_mysql_user(user):
    with get_conn_cursor() as cursor:
        sql = 'SELECT * FROM mysql.user WHERE User = %s'
        cursor.execute(sql, args=[user])
        return len(cursor.fetchall()) > 0

def validate_mysql_user_password(user, password):
    print '\nvalidate password for user "%s" ... \n' % user
    try:
        conn = MySQLdb.connect(host=conf[CONF_MYSQL_HOST],
                               user=user,
                               passwd=password)
    except MySQLdb.OperationalError, e:
        print e.args[1]
        return False
    else:
        conn.close()
        return True

def try_connect_db(user, password, db):
    try:
        MySQLdb.connect(host=conf[CONF_MYSQL_HOST], user=user, passwd=password, db=db)
    except MySQLdb.OperationalError, e:
        msg = e.args[1]

        print msg
        return False
    else:
        return True

def read_config(fn):
    '''Return a case sensitive ConfigParser by reading the file "fn"'''
    cp = ConfigParser()
    cp.optionxform = str
    cp.read(fn)

    return cp

### END of Common helper functions
####################

def setup_seahub_env():
    '''And PYTHONPATH and CCNET_CONF_DIR/SEAFILE_CONF_DIR to env, which is
    needed by seahub

    '''
    os.environ['CCNET_CONF_DIR'] = conf[CONF_CCNET_DIR]
    os.environ['SEAFILE_CONF_DIR'] = conf[CONF_SEAFILE_DIR]

    # pythonpath
    installpath = conf[CONF_INSTALL_PATH]
    pro_pylibs_dir = os.path.join(installpath, 'pro', 'python')
    extra_python_path = [
        pro_pylibs_dir,

        os.path.join(installpath, 'seahub', 'thirdpart'),
        os.path.join(installpath, 'seahub-extra'),
        os.path.join(installpath, 'seahub-extra', 'thirdpart'),

        os.path.join(installpath, 'seafile/lib/python2.6/site-packages'),
        os.path.join(installpath, 'seafile/lib64/python2.6/site-packages'),
        os.path.join(installpath, 'seafile/lib/python2.7/site-packages'),
        os.path.join(installpath, 'seafile/lib64/python2.7/site-packages'),
    ]

    for path in extra_python_path:
        prepend_env_value('PYTHONPATH', path)

    seafes_dir = os.path.join(pro_pylibs_dir, 'seafes')
    os.environ['SEAFES_DIR'] = seafes_dir

def read_seafile_data_dir(ccnet_dir):
    seafile_ini = os.path.join(ccnet_dir, 'seafile.ini')
    if not os.path.exists(seafile_ini):
        error('%s not found' % seafile_ini)

    with open(seafile_ini, 'r') as fp:
        seafile_data_dir = fp.read().strip()

    if not os.path.exists(seafile_data_dir):
        error('seafile.ini not found')

    return seafile_data_dir

def check_python_module(import_name, package_name, silent=False):
    if not silent:
        info('checking %s' % package_name)
    try:
        __import__(import_name)
    except ImportError:
        error('Python module "%s" not found. Please install it first' % package_name)

class InvalidAnswer(Exception):
    def __init__(self, msg):
        Exception.__init__(self)
        self.msg = msg
    def __str__(self):
        return self.msg

class Questions(object):
    '''A class to collect all questions asked to the user'''
    def ask_question(self, desc, key=None, note=None, default=None,
                     validate=None, yes_or_no=False, password=False):
        '''Ask a question, return the answer. The optional validate param is a
        function used to validate the answer. If yes_or_no is True, then a boolean
        value would be returned.

        '''
        assert key or yes_or_no
        if note:
            desc += '  (%s)' % note
        if default:
            desc += '\n' + ('[default "%s" ]' % default)
        else:
            if yes_or_no:
                desc += '\n[yes or no]'
            else:
                desc += '\n' + ('[%s ]' % key)

        desc += '  '
        while True:
            if password:
                answer = getpass.getpass(desc).strip()
            else:
                answer = raw_input(desc).strip()
            if not answer:
                if default:
                    print
                    return default
                else:
                    continue

            answer = answer.strip()

            if yes_or_no:
                if answer != 'yes' and answer != 'no':
                    print '\nPlease answer yes or no\n'
                    continue
                else:
                    return answer == 'yes'
            else:
                if validate:
                    try:
                        validate(answer)
                    except InvalidAnswer, e:
                        print highlight('\n%s\n' % e)
                        continue

            print
            return answer

    def ask_mysql_host(self):
        validate = None
        question = 'What is the host address of the mysql server?'
        key = 'mysql host'
        default = 'localhost'
        host = self.ask_question(question,
                                 key=key,
                                 default=default,
                                 validate=validate)

        # Force use tcp to connect to mysql server so that we can avoid the
        # difference of mysqld unix socket path on different linux distros
        if host == 'localhost':
            host = '127.0.0.1'

        return host

    def ask_mysql_root_password(self):
        def validate(password):
            if not validate_mysql_user_password('root', password):
                raise InvalidAnswer('root password is not corret')

        question = 'What is the password of the mysql server root user?'
        key = 'mysql root password'
        return self.ask_question(question,
                                 key=key,
                                 validate=validate,
                                 password=True)


    def ask_mysql_seafile_user(self):
        validate = None
        key = 'seafile mysql user'
        if conf[CONF_USE_EXISTING_DB]:
            question = 'What is the mysql user for seafile?'
            default = None
            note = None
        else:
            question = 'Which user do you want to use for all seafile databases?'
            note = 'This user will be created if not exists'
            default = 'seafile'
        return self.ask_question(question,
                                 key=key,
                                 note=note,
                                 default=default,
                                 validate=validate)

    def ask_mysql_seafile_password(self):
        user = conf[CONF_MYSQL_SEAFILE_USER]
        def validate(password):
            if conf[CONF_USE_EXISTING_DB]:
                if not validate_mysql_user_password(user, password):
                    raise InvalidAnswer('Password for user "%s" is not correct' % user)

        key = 'password for mysql user "%s"' % user
        if conf[CONF_USE_EXISTING_DB]:
            question = 'What is the password of mysql user "%s"?' % user
            default = None
        else:
            question = 'Which password do you want to use for the mysql seafile user?'
            default = 'seafile'
        return self.ask_question(question,
                                 key=key,
                                 default=default,
                                 validate=validate,
                                 password=True)

    def ask_db_name(self, program, default):
        def validate(db_name):
            if conf[CONF_USE_EXISTING_DB]:
                user = conf[CONF_MYSQL_SEAFILE_USER]
                password = conf[CONF_MYSQL_SEAFILE_PASSWORD]
                if not try_connect_db(user, password, db_name):
                    raise InvalidAnswer("Can't access database '%s' with user '%s' and password '%s'" \
                                        % (db_name, user, password))

        question = 'Which database do you want to use for %s ?' % program
        key = '%s database name' % program
        return self.ask_question(question,
                                 key=key,
                                 default=default,
                                 validate=validate)

    def ask_if_use_existing_db(self):
        def validate(choice):
            if choice not in ('1', '2'):
                raise InvalidAnswer('Please choose 1 or 2')

        question = '''\
1) Create ccnet/seafile/seahub databases for me
2) I have already created the databases
'''

        key = '1 / 2'
        default = '1'
        choice = self.ask_question(question,
                                   key=key,
                                   default=default,
                                   validate=validate)
        if choice == '1':
            return False
        else:
            return True

    def ask_admin_email(self):
        def validate(email):
            # whitespace is not allowed
            if re.match(r'[\s]', email) or not re.match(r'^.+@.*\..+$', email):
                raise InvalidAnswer('"%s" is not a valid email address' % email)

        key = 'seahub admin email'
        question = 'Please specify the email address for seahub admininstrator:'
        return self.ask_question(question,
                                 key=key,
                                 validate=validate)

    def ask_admin_password(self):
        def validate(password):
            password_again = self.ask_admin_password_again()
            if password_again != password:
                raise InvalidAnswer('Password mismatch')

        key = 'seahub admin password'
        question = 'Please specify the password for seahub admininstrator:'
        password = self.ask_question(question,
                                     key=key,
                                     password=True,
                                     validate=validate)

        return hashlib.sha1(password).hexdigest()

    def ask_admin_password_again(self):
        key = 'seahub admin password again'
        question = 'Please input the password again:'
        return self.ask_question(question,
                                 key=key,
                                 password=True)

Q = Questions()

def create_db(db_name):
    with get_conn_cursor() as cursor:
        sys.stdout.write('\ncreating database %s ...' % db_name)
        sql = "CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET = 'utf8'" % db_name
        cursor.execute(sql)
        sys.stdout.write('done\n')

    if conf[CONF_MYSQL_SEAFILE_USER] != 'root':
        with get_conn_cursor() as cursor:
            sys.stdout.write('\ngranting database permissions for %s ...' % db_name)
            sql = 'GRANT ALL PRIVILEGES ON `%(db_name)s`.* to `%(user)s`@`%(host)s`'
            args = dict(db_name=db_name, user=conf[CONF_MYSQL_SEAFILE_USER], host=conf[CONF_MYSQL_HOST])
            cursor.execute(sql % args)
            sys.stdout.write('done\n')

def report_db_init_info():
    pass

def init_databases():
    '''
    Create ccnet/seafile/seahub databases in mysql
    '''
    conf[CONF_MYSQL_HOST] = Q.ask_mysql_host()

    conf[CONF_USE_EXISTING_DB] = Q.ask_if_use_existing_db()

    if not conf[CONF_USE_EXISTING_DB]:
        conf[CONF_MYSQL_ROOT_PASSWORD] = Q.ask_mysql_root_password()
        conf[CONF_MYSQL_ROOT_CONN] = MySQLdb.connect(host=conf[CONF_MYSQL_HOST],
                                                     user='root',
                                                     passwd=conf[CONF_MYSQL_ROOT_PASSWORD])

    conf[CONF_MYSQL_SEAFILE_USER] = Q.ask_mysql_seafile_user()
    conf[CONF_MYSQL_SEAFILE_PASSWORD] = Q.ask_mysql_seafile_password()

    conf[CONF_DB_NAME_CCNET] = Q.ask_db_name('ccnet', 'ccnet-db')
    conf[CONF_DB_NAME_SEAFILE] = Q.ask_db_name('seafile', 'seafile-db')
    conf[CONF_DB_NAME_SEAHUB] = Q.ask_db_name('seahub', 'seahub-db')

    report_db_init_info()

    if not conf[CONF_USE_EXISTING_DB]:

        # create seafile user
        if not validate_mysql_user(conf[CONF_MYSQL_SEAFILE_USER]):
            create_seafile_db_user()

        # create databases
        create_db(conf[CONF_DB_NAME_CCNET])
        create_db(conf[CONF_DB_NAME_SEAFILE])
        create_db(conf[CONF_DB_NAME_SEAHUB])

def create_seafile_db_user():
    sql = 'CREATE USER %(user)s@%(host)s identified by %(password)s'
    args = dict(user=conf[CONF_MYSQL_SEAFILE_USER], host=conf[CONF_MYSQL_HOST], password=conf[CONF_MYSQL_SEAFILE_PASSWORD])

    with get_conn_cursor() as cursor:
        cursor.execute(sql, args=args)

@contextmanager
def get_conn_cursor(conn=None):
    '''A helper function for code like this:

        with get_conn_cursor(db) as cursor:
            cursor.execute(sql)
    '''
    if conn is None:
        conn = conf[CONF_MYSQL_ROOT_CONN]
    cursor = conn.cursor()
    yield cursor
    cursor.close()

def do_reset_admin():
    pass

def update_ccnet_conf():
    '''Update the "DataBase" section of ccnet.conf'''
    time.sleep(.3)
    print 'updating ccnet.conf ...'

    ccnet_conf = os.path.join(conf[CONF_CCNET_DIR], 'ccnet.conf')
    db_section = 'DataBase'

    KEY_TYPE = 'ENGINE'
    KEY_HOST = 'HOST'
    KEY_USER = 'USER'
    KEY_PASSWORD = 'PASSWORD'
    KEY_DB = 'DB'
    KEY_UNIX_SOCKET = 'UNIX_SOCKET'

    cp = read_config(ccnet_conf)

    if not cp.has_section(db_section):
        cp.add_section(db_section)

    cp.set(db_section, KEY_TYPE, 'mysql')
    cp.set(db_section, KEY_HOST, conf[CONF_MYSQL_HOST])
    cp.set(db_section, KEY_USER, conf[CONF_MYSQL_SEAFILE_USER])
    cp.set(db_section, KEY_PASSWORD, conf[CONF_MYSQL_SEAFILE_PASSWORD])
    cp.set(db_section, KEY_DB, conf[CONF_DB_NAME_CCNET])

    with open(ccnet_conf, 'w') as fp:
        cp.write(fp)

def update_seafile_conf():
    '''Update the "DataBase" section of ccnet.conf'''
    time.sleep(.3)
    print 'updating seafile.conf ...'

    seafile_conf = os.path.join(conf[CONF_SEAFILE_DIR], 'seafile.conf')
    db_section = 'database'

    KEY_TYPE = 'type'
    KEY_HOST = 'host'
    KEY_USER = 'user'
    KEY_PASSWORD = 'password'
    KEY_DB = 'db_name'
    KEY_UNIX_SOCKET = 'unix_socket'

    cp = read_config(seafile_conf)

    if not cp.has_section(db_section):
        cp.add_section(db_section)

    cp.set(db_section, KEY_TYPE, 'mysql')
    cp.set(db_section, KEY_HOST, conf[CONF_MYSQL_HOST])
    cp.set(db_section, KEY_USER, conf[CONF_MYSQL_SEAFILE_USER])
    cp.set(db_section, KEY_PASSWORD, conf[CONF_MYSQL_SEAFILE_PASSWORD])
    cp.set(db_section, KEY_DB, conf[CONF_DB_NAME_SEAFILE])

    with open(seafile_conf, 'w') as fp:
        cp.write(fp)

def update_seahub_settings_py():
    time.sleep(.3)
    print 'updating seahub_settings.py ...'

    seahub_settings = os.path.join(conf[CONF_TOP_DIR], 'seahub_settings.py')
    db_settings_template = '''\
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': '%(db)s',
        'USER': '%(user)s',
        'PASSWORD': '%(password)s',
        'HOST': '%(host)s',
        'OPTIONS': {
            'init_command': 'SET storage_engine=INNODB',
        }
    }
}
'''
    db_settings = db_settings_template % dict(db=conf[CONF_DB_NAME_SEAHUB],
                                              user=conf[CONF_MYSQL_SEAFILE_USER],
                                              password=conf[CONF_MYSQL_SEAFILE_PASSWORD],
                                              host=conf[CONF_MYSQL_HOST])

    with open(seahub_settings, 'a') as fp:
        fp.write(db_settings)

def seahub_syncdb():
    seahub_dir = conf[CONF_SEAHUB_DIR]
    argv = [
        get_python_executable(),
        'manage.py',
        'syncdb'
    ]
    if run_argv(argv, cwd=seahub_dir) != 0:
        error('Failed to create seahub database tables')

def set_seahub_admin():
    email = conf[CONF_ADMIN_PASSWORD] = Q.ask_admin_email()
    password = conf[CONF_ADMIN_PASSWORD] = Q.ask_admin_password()

    conn = MySQLdb.connect(host=conf[CONF_MYSQL_HOST],
                           user=conf[CONF_MYSQL_SEAFILE_USER],
                           passwd=conf[CONF_MYSQL_SEAFILE_PASSWORD],
                           db=conf[CONF_DB_NAME_CCNET])

    with get_conn_cursor(conn) as cursor:
        sql = '''\
CREATE TABLE IF NOT EXISTS EmailUser ( \
id INTEGER NOT NULL PRIMARY KEY AUTO_INCREMENT, \
email VARCHAR(255), passwd CHAR(41), \
is_staff BOOL NOT NULL, is_active BOOL NOT NULL, \
ctime BIGINT, UNIQUE INDEX (email)) \
ENGINE=INNODB'''

        cursor.execute(sql)

        sql = 'SELECT * from EmailUser WHERE email = %s'
        cursor.execute(sql, args=[email])
        if len(cursor.fetchall()) > 0:
            sql = 'UPDATE EmailUser SET is_staff = 1, passwd = %(password)s WHERE EMAIL = %(email)s'
        else:
            sql = '''INSERT INTO EmailUser(email, passwd, is_staff, is_active, ctime) \
            VALUES (%(email)s, %(password)s, 1, 1, 0)'''

        args = dict(email=email, password=password)
        cursor.execute(sql, args=args)

    conn.commit()
    conn.close()

    info('Successfully created your admin account')

def stop_seafile():
    pass

def do_switch_to_mysql():
    '''
    1. create ccnet/seafile/seahub/ databases
    2. modify ccnet.conf seafile.conf, seahub_settings.py
    3. run 'syncdb' to create seahub tables
    4. create admin
    '''
    stop_seafile()
    init_databases()
    update_ccnet_conf()
    update_seafile_conf()
    update_seahub_settings_py()
    seahub_syncdb()
    set_seahub_admin()

def validate_seafile_server_install():
    def error_not_found(path):
        error('"%s" not found' % path)

    def error_if_not_exists(path):
        if not os.path.exists(path):
            error_not_found(path)

    installpath = os.path.dirname(os.path.abspath(__file__))
    topdir = os.path.dirname(installpath)
    ccnet_dir = os.path.join(topdir, 'ccnet')
    seafile_dir = read_seafile_data_dir(ccnet_dir)
    seahub_dir = os.path.join(installpath, 'seahub')

    paths = [
        ccnet_dir,
        os.path.join(ccnet_dir, 'ccnet.conf'),

        seafile_dir,
        os.path.join(seafile_dir, 'seafile.conf'),

        seahub_dir,
        os.path.join(topdir, 'seahub_settings.py'),
    ]

    for path in paths:
        error_if_not_exists(path)

    conf[CONF_TOP_DIR] = topdir
    conf[CONF_INSTALL_PATH] = installpath
    conf[CONF_CCNET_DIR] = ccnet_dir
    conf[CONF_SEAFILE_DIR] = seafile_dir
    conf[CONF_SEAHUB_DIR]  = seahub_dir

def parse_args():
    parser = optparse.OptionParser()
    def long_opt(opt):
        return '--' + opt

    parser.add_option(long_opt(CONF_RESET_ADMIN),
                      dest=CONF_RESET_ADMIN,
                      action='store_true',
                      help='reset seafile admin account')

    options, remain = parser.parse_args()
    if remain:
        usage(parser.format_help())

    return options

# ----------------------------------------
# main function
# ----------------------------------------
def main():
    options = parse_args()
    warnings.filterwarnings('ignore', category=MySQLdb.Warning)

    validate_seafile_server_install()
    setup_seahub_env()
    if options.reset_admin:
        do_reset_admin()
    else:
        do_switch_to_mysql()

if __name__ == '__main__':
    main()
