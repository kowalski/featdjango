%global __python python

%{!?python_sitelib: %define python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")}
%{!?pyver: %define pyver %(%{__python} -c "import sys ; print sys.version[:3]")}
%define version 1.0.0

Name:           python-featdjango
Summary:        F3AT Django integration
Version:        %{version}
Release:        1%{?dist}
Source0:        featdjango-%{version}.tar.gz

Group:          Development/Languages
License:        GPL
URL:            http://www.flumotion.com

BuildRoot:      %{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)

BuildRequires:  python-devel >= 2.6
BuildRequires:  python-setuptools >= 0.6c9

Requires:       python-twisted-core
Requires:       python-twisted-web
Requires:       nsca-client
Requires:       python-feat

Provides:       %{name}

%description
Flumotion Asynchronous Autonomous Agent Toolkit and Django integration.

%prep
%setup -q -n featdjango-%{version}

%build
CFLAGS="$RPM_OPT_FLAGS" %{__python} setup.py build

%install
rm -rf $RPM_BUILD_ROOT
%{__python} setup.py install --skip-build --root=$RPM_BUILD_ROOT \
     --record=INSTALLED_FILES

%clean
rm -rf $RPM_BUILD_ROOT


%files
%defattr(-,root,root,-)

%doc README.rst

%{python_sitelib}/*


%changelog
* Mon Sep 29 2014 Thomas Vander Stichele <thomas at apestaart dot org>
- 1.0.0-1
- bump major version

* Fri Apr 25 2014 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.3.1-1
- new release

* Mon Mar 10 2014 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.3.0-1
- new release

* Tue Dec 24 2013 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.2.4-1
- new release

* Tue Dec 17 2013 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.2.3-1
- new release

* Wed Aug 14 2013 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.2.2-1
- new release

* Wed Jun 19 2013 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.2.1-1
- new release

* Fri May 31 2013 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.2.0-1
- new release

* Tue May 07 2013 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.1.2-1
- new release

* Mon Feb 11 2013 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.1.1-1
- new release

* Mon Dec 10 2012 Thomas Vander Stichele <thomas at apestaart dot org>
- 0.1.0-1
- first version
