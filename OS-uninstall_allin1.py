#!/usr/bin/env python
# -*- mode: python; coding: utf-8 -*-

import os
from os import system as run

# Uninstall packages
run ('yum remove -y "*openstack*" "*nova*" "*keystone*" "*glance*" "*cinder*" "*swift*" mysql mysql-server httpd')

# Clean up
run ('rm -rf /var/lib/mysql/ /var/lib/nova /etc/nova /etc/swift')
