#!/usr/bin/env python
# -*- mode: python; coding: utf-8 -*-

"""
Installs Openstack (all in one) using the latest Packstack package.
"""

import os, re
from os import system as run

VG_CINDER_DEV = '/dev/vdb'

ANS_REPLACEMENTS = [
    ('CONFIG_SWIFT_INSTALL=n', 'CONFIG_SWIFT_INSTALL=y'),
    ('CONFIG_NOVA_COMPUTE_PRIVIF=eth1', 'CONFIG_NOVA_COMPUTE_PRIVIF=eth0'),
    ('CONFIG_NOVA_NETWORK_PRIVIF=eth1', 'CONFIG_NOVA_NETWORK_PRIVIF=eth0')
]

PACKSTACK_HTTP_INDEX = "http://10.16.16.34/rpms/RPMS/noarch"
CIRROS_URL           = "https://launchpad.net/cirros/trunk/0.3.0/+download/cirros-0.3.0-x86_64-disk.img"

# Check dependencies
if not os.path.exists('/usr/bin/wget'):
    run ("yum install -y wget")

# Install Packstack
if not os.path.exists ('/usr/bin/packstack'):

    # Figure RPM to download
    index = os.popen ('wget -O - %(PACKSTACK_HTTP_INDEX)s'%(globals()), 'r').read()
    files = [f for f in re.findall (r'href="(.+?)"', index) if '.rpm' in f]
    http_rpm = '%s/%s' %(PACKSTACK_HTTP_INDEX, files[-1])

    # Download
    run ("wget %s" %(http_rpm))
    run ("rpm -i %s" %(os.path.basename(http_rpm)))

print "* Packstack installed"

# Check SSH keys
try:
    ssh_files = os.listdir (os.path.join (os.getenv('HOME'), '.ssh'))
except OSError:
    ssh_files = []

if not 'id_rsa' in ssh_files:
    run ("ssh-keygen -t rsa -N '' -f $HOME/.ssh/id_rsa")

print "* $HOME/.ssh/id_rsa: OK"

# Check authorized keys
try:
    auth = open(os.path.expanduser("~/.ssh/authorized_keys"),'r').read()
except IOError:
    auth = ''

pub = open(os.path.expanduser("~/.ssh/id_rsa.pub"),'r').read().strip()

if not pub in auth:
    open(os.path.expanduser("~/.ssh/authorized_keys",'a')).write(pub+'\n')

print "* $HOME/.ssh/authorized_keys: OK"

# Generate the answers file
run ("packstack --gen-answer-file=/tmp/ans.txt.orig")

# Configure it
ans = open('/tmp/ans.txt.orig','r').read()

for r in ANS_REPLACEMENTS:
    ans = ans.replace (r[0], r[1])

open('/tmp/ans.txt', 'w+').write(ans)

# Volume Group for Cinder
if not os.path.exists (VG_CINDER_DEV):
    assert False, "VG_CINDER_DEV=%(VG_CINDER_DEV)s doesn't exist"%(globals())

if not 'cinder-volumes' in os.popen('vgdisplay | grep "VG Name"').read():
    run ('vgcreate cinder-volumes %(VG_CINDER_DEV)s' %(globals()))

print "* Cinder volume ready"

# Install
run ('packstack --answer-file=/tmp/ans.txt')

# Download image for Glance
local_cirros = os.path.join ("/var/tmp", os.path.basename(CIRROS_URL))
run ("wget -c %s -O /%s"%(CIRROS_URL, local_cirros))

# Add a image to Glance
if not 'cirros' in os.popen("glance image-list").read():
    run ('source ~/keystonerc_admin ;' + \
         'env | grep OS_ ;' + \
         'glance image-create --name cirros --disk-format qcow2 --container-format bare --is-public 1 --copy-from %s' %(local_cirros))
