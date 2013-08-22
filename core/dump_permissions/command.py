import json
import pprint
import urllib
import logging

from boto.iam import IAMConnection
from boto.ec2 import EC2Connection
from core.common_arguments import add_credential_arguments
from core.utils.get_current_user import get_current_user, get_user_from_key


def cmd_arguments(subparsers):
    #
    # dump-role-permissions subcommand help
    #
    _help = 'Dump the permissions for the currently configured credentials'
    parser = subparsers.add_parser('dump-permissions', help=_help)
    
    add_credential_arguments(parser)
    
    return subparsers

def cmd_handler(args):
    '''
    Main entry point for the sub-command.
    
    :param args: The command line arguments as parsed by argparse
    '''
    logging.debug('Starting dump-permissions')
    
    # First, check if we're the root account
    if check_root_account(args.access_key, args.secret_key, args.token):
        return
    
    # Try to access this information from the IAM service, which can fail
    # because the current credentials have no access to that API
    success, permissions = check_via_iam(args.access_key, args.secret_key, args.token)
    if success:
        print_permissions(permissions)
        return
    
    # Bruteforce the permissions
    permissions = bruteforce_permissions(args.access_key, args.secret_key, args.token)
    print_permissions(permissions)

def print_permissions(permissions):
    if not permissions:
        logging.warn('No permissions could be dumped.')
        return
    
    for permission_obj in permissions:
        logging.info(pprint.pformat(permission_obj))

def bruteforce_permissions(access_key, secret_key, token):
    '''
    Will check the most common API calls and verify if we have access to them.
    '''
    # Some common actions are:
    #    u'autoscaling:Describe*',
    #    u'ec2:Describe*',
    #    u's3:Get*',
    #    u's3:List*',
    action_list = []
    permissions = []
    
    BRUTEFORCERS = (bruteforce_ec2_permissions,)
    
    for bruteforcer in BRUTEFORCERS:
        action_list.extend(bruteforcer(access_key, secret_key, token))
    
    bruteforced_perms = {u'Statement': [{u'Action': action_list,
                                         u'Effect': u'Allow',
                                         u'Resource': u'*'}],
                         u'Version': u'2012-10-17'}
    
    if action_list:
        permissions.append(bruteforced_perms)
    
    return permissions

def bruteforce_ec2_permissions(access_key, secret_key, token):
    actions = []
    
    try:
        conn = EC2Connection(aws_access_key_id=access_key,
                             aws_secret_access_key=secret_key,
                             security_token=token)
    except Exception, e:
        logging.debug('Failed to connect to EC2: "%s"' % e.error_message)
        return actions
    
    TESTS = [('DescribeImages', conn.get_all_images, (), {'owners': ['self',]}),
             ('DescribeInstances', conn.get_all_instances, (), {}),
             ('DescribeInstanceStatus', conn.get_all_instance_status, (), {}),]
    for api_action, method, args, kwargs in TESTS:
        try:
            method(*args, **kwargs)
        except Exception, e:
            logging.debug('%s is not allowed: "%s"' % (api_action, e.error_message))
        else:
            logging.debug('%s IS allowed' % api_action)
            actions.append(api_action)
    
    if not actions:
        logging.warn('No actions could be bruteforced.')

    return actions

def check_via_iam(access_key, secret_key, token):
    '''
    Connect to IAM and try to retrieve my policy.
    '''
    try:
        conn = IAMConnection(aws_access_key_id=access_key,
                             aws_secret_access_key=secret_key,
                             security_token=token)
    except Exception, e:
        logging.debug('Failed to connect to IAM: "%s"' % e.error_message)
        logging.debug('Account has no access to IAM')
        return False, None

    user = get_current_user(conn, access_key)
    
    if user is None:
        return False, None

    try:
        all_user_policies = conn.get_all_user_policies(user)
    except Exception, e:
        msg = 'Account has no privileges to get all user policies: "%s"'
        logging.debug(msg % e.error_message)
        return False, None

    policy_names = all_user_policies['list_user_policies_response']['list_user_policies_result']['policy_names']
    
    permissions = []
    
    for policy_name in policy_names:
        try:
            user_policy = conn.get_user_policy(user, policy_name)
        except:
            msg = 'Account has no privileges to get user policy: "%s"'
            logging.debug(msg % e.error_message)
            return False, None
        else:
            policy_document = user_policy['get_user_policy_response']['get_user_policy_result']['policy_document']
            policy_document = urllib.unquote(policy_document)
            policy_document = json.loads(policy_document)
            permissions.append(policy_document)
    
    return True, permissions


def check_root_account(access_key, secret_key, token):
    '''
    Do we have root account? The trick here is to enumerate all users and
    check if the access key provided is in the ones assigned to a user. This
    works because root accounts don't have a user associated to them.
    
    http://docs.aws.amazon.com/general/latest/gr/root-vs-iam.html
    '''
    if token:
        # Instance profiles don't have root account level
        return False
    
    try:
        conn = IAMConnection(aws_access_key_id=access_key,
                             aws_secret_access_key=secret_key,
                             security_token=token)
    except Exception, e:
        # Root has access to IAM
        logging.debug('Failed to connect to IAM: "%s"' % e.error_message)
        logging.debug('Not an AWS root account')
        return False
    
    try:
        conn.get_account_summary()
    except Exception, e:
        # Root has access to IAM account summary
        logging.debug('Failed to retrieve IAM account summary: "%s"' % e.error_message)
        logging.debug('Not an AWS root account')
        return False
    
    try:
        user = get_user_from_key(conn, access_key)
    except Exception, e:
        # Root has access to IAM
        logging.debug('Failed to enumerate users and access keys: "%s"' % e.error_message)
        logging.debug('Not an AWS root account')
        return False
    else:
        if user is not None:
            logging.debug('These credentials belong to %s, not to the root account' % user)
            return False
            
    logging.info('The provided credentials are for an AWS root account! These'\
                 ' credentials have ALL permissions.')
    
    return True