import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):
    print(msg.payload.decode())

client = mqtt.Client()
client.username_pw_set("rowdwwyg:rowdwwyg", "yUSQiP-iSE2Tm3sXQpp2sDvy7yJGMRzG")
client.connect("kingfisher.lmq.cloudamqp.com", 8883, 60)
client.subscribe("iot/esp32_01/env")
client.on_message = on_message(client=client, userdata=None, msg=None)

client.loop_forever()
