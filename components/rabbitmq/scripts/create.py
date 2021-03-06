#!/usr/bin/env python

import os
import time
from os.path import join, dirname

from cloudify import ctx

ctx.download_resource(
    join('components', 'utils.py'),
    join(dirname(__file__), 'utils.py'))
import utils  # NOQA


CONFIG_PATH = 'components/rabbitmq/config'
RABBITMQ_SERVICE_NAME = 'rabbitmq'

ctx_properties = utils.ctx_factory.create(RABBITMQ_SERVICE_NAME)


def check_if_user_exists(username):
    if username in utils.sudo(
            ['rabbitmqctl', 'list_users'], retries=5).aggr_stdout:
        return True
    return False


def _clear_guest_permissions_if_guest_exists():
    if check_if_user_exists('guest'):
        ctx.logger.info('Disabling RabbitMQ guest user...')
        utils.sudo(['rabbitmqctl', 'clear_permissions', 'guest'], retries=5)
        utils.sudo(['rabbitmqctl', 'delete_user', 'guest'], retries=5)


def _create_user_and_set_permissions(rabbitmq_username,
                                     rabbitmq_password):
    if not check_if_user_exists(rabbitmq_username):
        ctx.logger.info('Creating new user and setting permissions...'.format(
            rabbitmq_username, rabbitmq_password))
        utils.sudo(['rabbitmqctl', 'add_user',
                    rabbitmq_username, rabbitmq_password])
        utils.sudo(['rabbitmqctl', 'set_permissions',
                    rabbitmq_username, '.*', '.*', '.*'], retries=5)
        utils.sudo(['rabbitmqctl', 'set_user_tags', rabbitmq_username,
                    'administrator'])


def _install_rabbitmq():
    erlang_rpm_source_url = ctx_properties['erlang_rpm_source_url']
    rabbitmq_rpm_source_url = ctx_properties['rabbitmq_rpm_source_url']
    # TODO: maybe we don't need this env var
    os.putenv('RABBITMQ_FD_LIMIT',
              str(ctx_properties['rabbitmq_fd_limit']))
    rabbitmq_log_path = '/var/log/cloudify/rabbitmq'
    rabbitmq_username = ctx_properties['rabbitmq_username']
    rabbitmq_password = ctx_properties['rabbitmq_password']

    ctx.logger.info('Installing RabbitMQ...')
    utils.set_selinux_permissive()

    utils.copy_notice(RABBITMQ_SERVICE_NAME)
    utils.mkdir(rabbitmq_log_path)

    utils.yum_install(erlang_rpm_source_url,
                      service_name=RABBITMQ_SERVICE_NAME)
    utils.yum_install(rabbitmq_rpm_source_url,
                      service_name=RABBITMQ_SERVICE_NAME)

    utils.logrotate(RABBITMQ_SERVICE_NAME)

    utils.systemd.configure(RABBITMQ_SERVICE_NAME)

    ctx.logger.info('Configuring File Descriptors Limit...')
    utils.deploy_blueprint_resource(
        '{0}/rabbitmq_ulimit.conf'.format(CONFIG_PATH),
        '/etc/security/limits.d/rabbitmq.conf',
        RABBITMQ_SERVICE_NAME)

    utils.deploy_blueprint_resource(
        '{0}/rabbitmq-definitions.json'.format(CONFIG_PATH),
        '/etc/rabbitmq/definitions.json',
        RABBITMQ_SERVICE_NAME)

    utils.systemd.systemctl('daemon-reload')

    utils.chown('rabbitmq', 'rabbitmq', rabbitmq_log_path)

    # rabbitmq restart exits with 143 status code that is valid in this case.
    utils.systemd.restart(RABBITMQ_SERVICE_NAME, ignore_failure=True)

    time.sleep(10)
    utils.wait_for_port(5672)

    ctx.logger.info('Enabling RabbitMQ Plugins...')
    # Occasional timing issues with rabbitmq starting have resulted in
    # failures when first trying to enable plugins
    utils.sudo(['rabbitmq-plugins', 'enable', 'rabbitmq_management'],
               retries=5)
    utils.sudo(['rabbitmq-plugins', 'enable', 'rabbitmq_tracing'], retries=5)

    _clear_guest_permissions_if_guest_exists()
    _create_user_and_set_permissions(rabbitmq_username, rabbitmq_password)

    utils.deploy_blueprint_resource(
        '{0}/rabbitmq.config'.format(CONFIG_PATH),
        '/etc/rabbitmq/rabbitmq.config',
        RABBITMQ_SERVICE_NAME, user_resource=True)

    utils.systemd.stop(RABBITMQ_SERVICE_NAME, retries=5)


def main():
    broker_ip = ctx.instance.host_ip
    _install_rabbitmq()
    ctx.instance.runtime_properties['rabbitmq_endpoint_ip'] = broker_ip


main()
