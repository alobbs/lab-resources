#!/usr/bin/env python

# AUTHORS:
#  Derek Higgins <derekh@redhat.com>
#  Alvaro Lopez Ortega <alvaro@redhat.com>

import os, sys, time, subprocess, socket, contextlib, argparse, select, fcntl

from novaclient.v1_1.client import Client
from novaclient.v1_1.keypairs import KeypairManager
from novaclient.v1_1.floating_ips import FloatingIPManager

DEFAULT_FLAVOR   = '3'
DEFAULT_IMAGE_ID = 'dad24449-4f9b-46a5-ac3b-a01da67de2dc' # RHEL
INSTALL_TYPES    = ('rhos', 'epel', 'bare')
CIRROS_URL       = "https://launchpad.net/cirros/trunk/0.3.0/+download/cirros-0.3.0-x86_64-disk.img"

# Package repositories
URL_RHEL_BOS  = 'http://download.lab.bos.redhat.com/released/RHEL-6/6.3/Server/x86_64/os/'
URL_RHEL_LOAD = os.path.join (URL_RHEL_BOS, 'LoadBalancer/')
URL_RHEL_HA   = os.path.join (URL_RHEL_BOS, 'HighAvailability/')
URL_RHEL_RS   = os.path.join (URL_RHEL_BOS, 'ResilientStorage/')
URL_RHEL_FS   = os.path.join (URL_RHEL_BOS, 'ScalableFileSystem')
URL_RHEL_OPT  = 'http://download.lab.bos.redhat.com/released/RHEL-6/6.3/Server/optional/x86_64/os/'
URL_RHOS      = 'http://download.lab.bos.redhat.com/rel-eng/OpenStack/Folsom/latest/x86_64/os/'


class ScriptRunner:
    SSH_PARAMS = ["-o", "StrictHostKeyChecking=no",
                  "-o", "UserKnownHostsFile=/dev/null",
                  "-o", "PasswordAuthentication=no"]

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
            obj = subprocess.Popen(["ssh"] + self.SSH_PARAMS + ["root@%s"%self.ip, "bash -x"],
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
parser.add_argument ('--image_id', action="store",      default=DEFAULT_IMAGE_ID, help="Image ID to deploy (Default: %s)"%(DEFAULT_IMAGE_ID))
parser.add_argument ('--flavor',   action="store",      default=DEFAULT_FLAVOR,   help="Image flavor (Default: %s)"%(DEFAULT_FLAVOR))
parser.add_argument ('--name',     action="store",      default=None,             help="Name of the instance (Default: automatically generated)")
parser.add_argument ('--install',  action="store",      default=INSTALL_TYPES[0], help="Post installation: %s. (Default: %s)" %(', '.join(INSTALL_TYPES), INSTALL_TYPES[0]))
parser.add_argument ('--ssh',      action="store_true", default=False,            help="Log-in the VM after when the installation is finished. (Default: No)")

ns = parser.parse_args()
if not ns:
    print ("ERROR: Couldn't parse parameters")
    raise SystemExit

assert ns.install in INSTALL_TYPES, "Invalid --install parameter. Options: %s" %(str(INSTALL_TYPES))

ns.install = ns.install.lower()
ns.name    = ns.name or "%s_%s"%(ns.install, time.strftime("%Y-%m-%d_%H:%M"))

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
    sys.stdout.write ('%s: Creating VM [' %(ns.name) + '#'*s + ' '*(20-s) + ']%s' %("\r\n"[s==20]))
    sys.stdout.flush()
    time.sleep(1)

print ("%s: ID %s" %(ns.name, server.id))

# Assign Floating IP. Create a new one if necessary
floating_ips = [ip for ip in client.floating_ips.list() if not ip.instance_id]
if not floating_ips:
    floating_mgr = FloatingIPManager (client)
    fip = floating_mgr.create()
else:
    fip = floating_ips[0]

if len(server.networks["novanetwork"]) < 2:
    server.add_floating_ip(fip)

print ("%s: Floating IP %s" %(ns.name, fip.ip))

# Wait until the SSH service is up
server = client.servers.get(server.id)
ipaddress = server.networks["novanetwork"][1]
print "%s: Waiting for it to come up"%(ns.name),

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


def repo_entry (name, url):
    return "[%(name)s]\\nname=%(name)s\\nbaseurl=%(url)s\\nenabled=1\\ngpgcheck=0\\n"%(locals())


# Scripts execution
def install_basics (run):
    run.append("echo -e '%s' > /etc/yum.repos.d/rhel-bos.repo" %('\n'.join(repos)))
    run.append("rpm -q epel-release-6-7 || rpm -Uvh http://download.fedoraproject.org/pub/epel/6/i386/epel-release-6-7.noarch.rpm")

def install_openstack_pre (run):
    run.ifnotexists("/root/.ssh/id_rsa", "ssh-keygen -f /root/.ssh/id_rsa -N ''")
    run.append("cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys")
    run.append("yum install -y git screen")

    run.ifnotexists("packstack", "git clone --recursive git://github.com/fedora-openstack/packstack.git")

    run.append("vgcreate cinder-volumes /dev/vdb")
    run.append("cd packstack")

    run.append("./packstack --gen-answer-file=ans.txt")

    run.append("sed -i -e 's/^CONFIG_KEYSTONE_ADMINPASSWD=.*/CONFIG_KEYSTONE_ADMINPASSWD=123456/g' ans.txt")
    run.append("sed -i -e 's/^CONFIG_NOVA_COMPUTE_PRIVIF=.*/CONFIG_NOVA_COMPUTE_PRIVIF=eth0/g' ans.txt")
    run.append("sed -i -e 's/^CONFIG_NOVA_NETWORK_PRIVIF=.*/CONFIG_NOVA_NETWORK_PRIVIF=eth0/g' ans.txt")
    run.append("sed -i -e 's/^CONFIG_SWIFT_INSTALL=.*/CONFIG_SWIFT_INSTALL=y/g' ans.txt")

def install_openstack_rhos (run):
    run.append("echo -e '%s' > /etc/yum.repos.d/folsom.repo"%(repo_entry ('rhos', URL_RHOS)))
    run.append("sed -i -e 's/^CONFIG_USE_EPEL=.*/CONFIG_USE_EPEL=n/g' ans.txt")

def install_openstack_post (run):
    run.append("./packstack --answer-file=ans.txt")

    run.append(". ~/keystonerc_admin")
    run.append("glance image-create --name cirros --disk-format qcow2 --container-format bare --is-public 1 --copy-from %s"%(CIRROS_URL))


target = ns.install.lower()
remote_server = ScriptRunner(ipaddress)

repos = [repo_entry ('rhel-bos',         URL_RHEL_BOS),
         repo_entry ('rhel-bos-opt',     URL_RHEL_OPT),
         repo_entry ('rhel-bos-HA',      URL_RHEL_HA),
         repo_entry ('rhel-bos-load',    URL_RHEL_LOAD),
         repo_entry ('rhel-bos-storage', URL_RHEL_RS),
         repo_entry ('rhel-bos-FS',      URL_RHEL_FS)]

# Build list of commands to execute
install_basics (remote_server)

if target == 'bare':
    pass
elif target == 'rhos':
    install_openstack_pre  (remote_server)
    install_openstack_rhos (remote_server)
    install_openstack_post (remote_server)
elif target == 'epel':
    install_openstack_pre  (remote_server)
    install_openstack_post (remote_server)

# Execute it
remote_server.execute()

print "==>", ipaddress

if ns.ssh:
    os.system ("ssh %s root@%s" %(' '.join(ScriptRunner.SSH_PARAMS), fip.ip))
