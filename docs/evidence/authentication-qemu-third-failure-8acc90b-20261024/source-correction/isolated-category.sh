set -eu
auth_name=$1
AUTH_LABEL=fixture
    case "$auth_name" in
        unmapped|retired)
            grep -E 'peer closed connection without sending TLS close_notify|Connection reset by peer \(os error 104\)' "$2" > /dev/null ;;
        *)
            grep -E 'permission denied: secure server authentication failed|TLS handshake failed|received fatal alert: (CertificateExpired|UnsupportedCertificate|BadCertificate|UnknownCA|AccessDenied|CertificateUnknown)' "$2" > /dev/null ;;
    esac
