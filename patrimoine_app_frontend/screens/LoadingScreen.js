// app/screens/LoadingScreen.js
import React from "react";
import { View, StyleSheet, Dimensions } from "react-native";
import LottieView from "lottie-react-native";

export default function LoadingScreen() {
  return (
    <View style={styles.container}>
      <LottieView
        source={require("../assets/Finance.json")}  // Assure-toi que le fichier est bien placé ici
        autoPlay
        loop
        style={styles.animation}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: "#fff"
  },
  animation: {
    width: Dimensions.get("window").width * 0.7,
    height: 300
  }
});
