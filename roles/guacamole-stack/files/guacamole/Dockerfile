ARG GUACAMOLE_VERSION=1.5.6
FROM guacamole/guacamole:${GUACAMOLE_VERSION}

ARG GUACAMOLE_VERSION=1.5.6

COPY guacamole-auth-json.jar \
     /opt/guacamole-json/guacamole-auth-json-${GUACAMOLE_VERSION}.jar

COPY start-custom.sh /start-custom.sh

USER root
RUN chmod +x /start-custom.sh

CMD ["/start-custom.sh"]
