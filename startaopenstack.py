#!/usr/bin/env python

# AUTHORS:
#  Derek Higgins <derekh@redhat.com>
#  Alvaro Lopez Ortega <alvaro@redhat.com>

import os, prettytable, sys, time, subprocess, socket, contextlib, argparse, select, fcntl

from novaclient.v1_1.client import Client
from novaclient.v1_1.keypairs import KeypairManager
from novaclient.v1_1.floating_ips import FloatingIPManager

DEFAULT_FLAVOR   = '3'
DEFAULT_IMAGE_ID = 'dad24449-4f9b-46a5-ac3b-a01da67de2dc' # RHEL
DEFAULT_NAME     = 'instance_%s' %(str(int(time.time())))

class ScriptRunner:
    def __init__(self, ip=None):
        self.ip     = ip
        self.script = []

    def append(self, s):
        self.script.append(s)

    def execute(self):
        # Build the script
        script = "\n".join(self.script)
        script = "function t(){ exit $? ; } \n trap t ERR \n" + script

        # Create new process
        _PIPE = subprocess.PIPE  # pylint: disable=E1101
        if self.ip:
            obj = subprocess.Popen(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "PasswordAuthentication=no", "root@%s"%self.ip, "bash -x"],
                                   stdin=_PIPE, stdout=sys.stdout, stderr=sys.stderr,
                                   close_fds=True, shell=False)
        else:
            obj = subprocess.Popen(["bash", "-x"],
                                   stdin=_PIPE, stdout=_PIPE, stderr=_PIPE,
                                   close_fds=True, shell=False)

        # Start the execution
        print "# ============ ssh : %r =========="%self.ip
        stdoutdata, stderrdata = obj.communicate(script)

        # Check the return code
        _returncode = obj.returncode
        if _returncode:
            print "============= STDERR ============="
            print stderrdata
            raise Exception ("Error running remote script")

    def template(self, src, dst, varsdict):
        with open(src) as fp:
            self.append("cat > %s <<- EOF\n%s\nEOF\n"%(dst, fp.read()%varsdict))

    def ifnotexists(self, fn, s):
        self.append("[ -e %s ] || %s"%(fn, s))

    def ifexists(self, fn, s):
        self.append("[ -e %s ] && %s || echo"%(fn, s))


# Process command line arguments
parser = argparse.ArgumentParser()
parser.add_argument ('--image_id', action="store", default=DEFAULT_IMAGE_ID, help="Image ID to deploy (Default: %s)"%(DEFAULT_IMAGE_ID))
parser.add_argument ('--flavor',   action="store", default=DEFAULT_FLAVOR,   help="Image flavor (Default: %s)"%(DEFAULT_FLAVOR))
parser.add_argument ('--name',     action="store", default=DEFAULT_NAME,     help="Instance name (Automatically chosen: %s)"%(DEFAULT_NAME))
parser.add_argument ('--vanilla',  action="store_true", default=False,       help="Just deploy the vanilla instance. Do not install OpenStack on it.")

ns = parser.parse_args()
if not ns:
    print ("ERROR: Couldn't parse parameters")
    raise SystemExit

# Nova client
client = Client(os.environ['OS_USERNAME'],
                os.environ['OS_PASSWORD'],
                os.environ['OS_TENANT_NAME'],
                os.environ['OS_AUTH_URL'],
                region_name  = 'RegionOne',
                service_type = 'compute')

# Figure out a keypair name
keypair = KeypairManager(client)
assert keypair.list(), 'You have to add at least one keypair (Hint: "nova keypair-add")'
keypair_name = keypair.list()[0].id

# Create the remote instance
server = client.servers.create(name     = ns.name,
                               image    = ns.image_id,
                               flavor   = ns.flavor,
                               key_name = keypair_name)

# Wait for the server to be created
for s in range(21):
    sys.stdout.write ('Please, wait [' + '#'*s + ' '*(20-s) + ']%s' %("\r\n"[s==20]))
    sys.stdout.flush()
    time.sleep(1)

# Assign Floating IP. Create a new one if necessary
floating_ips = [ip for ip in client.floating_ips.list() if not ip.instance_id]
if not floating_ips:
    floating_mgr = FloatingIPManager (client)
    fip = floating_mgr.create()
else:
    fip = floating_ips[0]

if len(server.networks["novanetwork"]) < 2:
    server.add_floating_ip(fip)

# Wait until the SSH service is up
server = client.servers.get(server.id)
ipaddress = server.networks["novanetwork"][1]
print "Waiting for %s (%s) to come up"%(server.id, ipaddress),

for n in range(30):
    try:
        s = socket.socket (socket.AF_INET, socket.SOCK_STREAM)
        with contextlib.closing(s):
            s.connect ((ipaddress, 22))
            print ''
    except Exception:
        sys.stdout.write('.')
        sys.stdout.flush()
        time.sleep(5)
    else:
        break
else:
        print "ERROR: Server never came up??"
        raise SystemExit

# Scripts execution
remote_server = ScriptRunner(ipaddress)

remote_server.append("echo -e '[rhel-bos]\nname=rhel-bos\nbaseurl=http://download.lab.bos.redhat.com/released/RHEL-6/6.3/Server/x86_64/os/\nenabled=1\ngpgcheck=0\n\n[rhel-bos-opt]\nname=rhel-bos-opt\nbaseurl=http://download.lab.bos.redhat.com/released/RHEL-6/6.3/Server/optional/x86_64/os/\nenabled=1\ngpgcheck=0' > /etc/yum.repos.d/rhel-bos.repo")
remote_server.append("rpm -q epel-release-6-7 || rpm -Uvh http://download.fedoraproject.org/pub/epel/6/i386/epel-release-6-7.noarch.rpm")

if ns.vanilla:
   remote_server.execute()
   print "==>", ipaddress
   raise SystemExit

remote_server.ifnotexists("/root/.ssh/id_rsa", "ssh-keygen -f /root/.ssh/id_rsa -N ''")
remote_server.append("cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys")
remote_server.append("yum install -y git cracklib-python screen puppet")

remote_server.ifnotexists("packstack", "git clone -b cinder-support git://github.com/derekhiggins/packstack.git")
remote_server.ifnotexists("installer", "git clone git://github.com/derekhiggins/installer.git")

# custom repo ?
remote_server.append('sed -i -e \'s/.*puppetlabs-swift.git.*/("https:\/\/github.com\/derekhiggins\/puppetlabs-swift.git", "swift", "jtopjian-puppetlabs-rebase"),/g\' packstack/plugins/puppet_950.py')
remote_server.append('sed -i -e \'s/.*puppetlabs-cinder.git.*/("https:\/\/github.com\/derekhiggins\/puppetlabs-cinder.git", "cinder", "targets-conf"),/g\' packstack/plugins/puppet_950.py')
remote_server.append("vgcreate cinder-volumes /dev/vdb")
remote_server.append("cd installer")

remote_server.append("sed -i -e 's/^DIR_PROJECT_DIR.*/DIR_PROJECT_DIR = \"..\/packstack\"/g' basedefs.py")

remote_server.append("python run_setup.py --gen-answer-file=ans.txt")

remote_server.append("sed -i -e 's/^CONFIG_KEYSTONE_ADMINPASSWD=.*/CONFIG_KEYSTONE_ADMINPASSWD=123456/g' ans.txt")
remote_server.append("sed -i -e 's/^CONFIG_LIBVIRT_TYPE=.*/CONFIG_LIBVIRT_TYPE=qemu/g' ans.txt")
remote_server.append("sed -i -e 's/^CONFIG_NOVA_COMPUTE_PRIVIF=.*/CONFIG_NOVA_COMPUTE_PRIVIF=eth0/g' ans.txt")
remote_server.append("sed -i -e 's/^CONFIG_NOVA_NETWORK_PRIVIF=.*/CONFIG_NOVA_NETWORK_PRIVIF=eth0/g' ans.txt")
remote_server.append("sed -i -e 's/^CONFIG_SWIFT_INSTALL=.*/CONFIG_SWIFT_INSTALL=y/g' ans.txt")

# Use RHOS
remote_server.append("echo -e '[rhos]\nname=rhos\nbaseurl=http://download.lab.bos.redhat.com/rel-eng/OpenStack/Folsom/latest/x86_64/os/\nenabled=1\ngpgcheck=0\n\n' > /etc/yum.repos.d/folsom.repo")

remote_server.append("python run_setup.py --answer-file=ans.txt")

remote_server.append(". ~/keystonerc_admin")
remote_server.append("glance image-create --name cirros --disk-format qcow2 --container-format bare --is-public 1 --copy-from https://launchpad.net/cirros/trunk/0.3.0/+download/cirros-0.3.0-x86_64-disk.img")

remote_server.execute()

print "==>", ipaddress
#server.delete()
