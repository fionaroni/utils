#!/usr/bin/python2.7
"""
Force the Disable Comments plugin onto an existing Wordpress install.

This needs to be run as root; try

    sudo `which bludgeon.py` /path/to/user/wp

This also needs a MySQL root password; you can provide it on the
command line or use a standard MySQL configuration file
(default ~/.my.cnf)
"""

from ConfigParser import ConfigParser
from contextlib import closing
from contextlib import contextmanager
from subprocess import CalledProcessError
from subprocess import check_output
from subprocess import STDOUT
from os.path import exists
from os.path import expanduser
from os.path import expandvars
from os.path import join
from pwd import getpwuid
from urllib2 import urlopen
import argparse
import getpass
import logging
import os
import re
import sys

# Load it in main
update = None

logger = logging.getLogger(__name__)

class MySQL(object):
    user = "root"
    magic = "UkVQTEFDRSBJTlRPIGB3cF9vcHRpb25zYCAoYG9wdGlvbl9uYW1lYCwgYG9wdGlvbl92YWx1ZWAsIGBhdXRvbG9hZGApIFZBTFVFUyAoJ2Rpc2FibGVfY29tbWVudHNfb3B0aW9ucycsJ2E6NDp7czoxOTpcImRpc2FibGVkX3Bvc3RfdHlwZXNcIjthOjM6e2k6MDtzOjQ6XCJwb3N0XCI7aToxO3M6NDpcInBhZ2VcIjtpOjI7czoxMDpcImF0dGFjaG1lbnRcIjt9czoxNzpcInJlbW92ZV9ldmVyeXdoZXJlXCI7YjoxO3M6OTpcInBlcm1hbmVudFwiO2I6MDtzOjEwOlwiZGJfdmVyc2lvblwiO2k6NTt9JywneWVzJyk7"

    def __init__(self, target_user, password):
        self.target_user = target_user
        self.password = password

    def disable_comments(self):
        """Black magic MySQL incantations.

        The query is:
            REPLACE INTO `wp_options` (`option_name`, `option_value`, `autoload`) VALUES ('disable_comments_options','a:4:{s:19:\"disabled_post_types\";a:3:{i:0;s:4:\"post\";i:1;s:4:\"page\";i:2;s:10:\"attachment\";}s:17:\"remove_everywhere\";b:1;s:9:\"permanent\";b:0;s:10:\"db_version\";i:5;}','yes');
        but I can't deal with the escaping.
        """
        check_output(
            "echo {magic} | base64 -d | mysql -uroot -p{password} {user}".format(
                magic=self.magic,
                password=self.password,
                user=self.target_user,
            ),
            shell=True,
        )


class Wordpress(object):
    script_name = "wp-cli.phar"
    download_path = "https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar"

    class Module(object):
        def __init__(self, parent, name):
            self.parent = parent
            self.name = name

        def _call(self, action_name, *args, **kwargs):
            return self.parent._call(self.name, action_name, *args, **kwargs)

        def __getattr__(self, name):
            def callable(*args, **kwargs):
                return self._call(name.replace("_", "-"), *args, **kwargs)
            return callable


    def __init__(self, wp_path):
        self.wpcli_bin = os.path.join(script_basename, "wp-cli.phar")
        self.download()

        self.wp_path = expandvars(expanduser(wp_path))
        logger.info("Wordpress install at {}".format(self.wp_path))

        os.chdir(self.wp_path)

    def _call(self, mod_name, action_name, *args, **kwargs):
        kwargs = [
            "--{option_name}".format(
                option_name=option_name,
            ) if option_value == True else
            "--{option_name}=\"{option_value}\"".format(
                option_name=option_name,
                option_value=option_value,
            )
            for option_name, option_value in kwargs.items()
        ]
        kwargs = " ".join(kwargs)
        if any(kwargs):
            kwargs = " " + kwargs

        args = " ".join(map(str, args))
        if any(args):
            args = " " + args

        cmd = "{bin} --allow-root {module} {action}{kwargs}{args}".format(
            bin=self.wpcli_bin,
            module=mod_name,
            action=action_name,
            kwargs=kwargs,
            args=args,
        )
        logger.debug(cmd)

        try:
            return check_output(cmd, shell=True, stderr=STDOUT).strip()
        except CalledProcessError as ex:
            map(logger.error, ex.output.strip().split('\n'))
            raise Exception("Error raised by WP-CLI")

    def __getattr__(self, name):
        """Create a callable which proxies through to WP-CLI.
        """
        return Wordpress.Module(self, name)

    def download(self):
        """Downloads the wp-cli script if necessary"""
        script_full_path = join(script_basename, self.script_name)

        if not exists(script_full_path):
            logger.info("Downloading wp-cli script...")
            with closing(urlopen(self.download_path)) as remote:
                with open(os.path.join(script_basename, self.script_name), 'wb') as local:
                    local.write(remote.read())
            os.chmod(script_full_path, 0755)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Filesystem path to a Wordpress installation")
    parser.add_argument("--mysql-password", help="MySQL root password")
    parser.add_argument("--mysql-config", default="~/.my.cnf", help="MySQL user configuration file")

    args = parser.parse_args()

    if os.geteuid() != 0:
        parser.error("This utility should be run as root.")

    mysql_password = None

    if args.mysql_password:
        logger.info("Using MySQL root password from command line")
        mysql_password = args.mysql_password

    real_path = expandvars(expanduser(args.mysql_config))
    if exists(real_path):
        logger.info("Using MySQL config file from {}".format(real_path))
        mysql_config = ConfigParser()
        mysql_config.read(real_path)
        mysql_password = mysql_config.get("mysql", "password")

    if not mysql_password:
        parser.error("No MySQL root password could be loaded.")

    stat = os.stat(args.path)
    mysql_user = getpwuid(stat.st_uid).pw_name

    logger.info("Using directory owner {} as MySQL user".format(mysql_user))

    wp = Wordpress(args.path)
    mysql = MySQL(mysql_user, mysql_password)

    logger.info("Wordpress version {}".format(wp.core.version()))

    # Don't check, just install.
    logger.info("Installing Disable Comments plugin")
    map(
        logger.info,
        wp.plugin.install("disable-comments", activate=True).strip().split('\n'),
    )

    # Update it too, just in case.
    logger.info("Updating Disable Comments plugin")
    map(
        logger.info,
        wp.plugin.update("disable-comments").strip().split('\n'),
    )

    logger.info("Writing plugin settings")
    mysql.disable_comments()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] %(message)s')
    script_basename = os.path.dirname(os.path.realpath(__file__))
    logging.info("Script directory is {}".format(script_basename))

    main()
