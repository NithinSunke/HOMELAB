#!/bin/bash

# Check if the username is provided as an argument
if [ -z "$1" ]; then
  echo "Usage: $0 <username>"
  exit 1
fi

# Variables
USERNAME=$1
PASSWORD=$1
SUDO_FILE="/etc/sudoers.d/$USERNAME"

# Create the new user
useradd -m -s /bin/bash "$USERNAME"
if [ $? -ne 0 ]; then
  echo "Failed to create user $USERNAME"
  exit 1
fi

# Set password for the new user (optional, uncomment if needed)
# echo "$USERNAME:password" | chpasswd

# Create the sudoers file for the user with appropriate permissions
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > "$SUDO_FILE"
if [ $? -ne 0 ]; then
  echo "Failed to create sudoers file for $USERNAME"
  exit 1
fi

# Change the permissions of the sudoers file
chmod 0440 "$SUDO_FILE"
if [ $? -ne 0 ]; then
  echo "Failed to set permissions for sudoers file"
  exit 1
fi

# Set the password for the user
echo "$USERNAME:$PASSWORD" | chpasswd

# Check if the password was set successfully
if [ $? -eq 0 ]; then
  echo "Password for user $USERNAME has been set successfully."
else
  echo "Failed to set password for user $USERNAME."
  exit 1
fi


# Output success message
echo "User $USERNAME created and sudo privileges granted."

#install git on target machine.
yum  install git -y
if [ $? -ne 0 ]; then
  echo "git is installed.."
  exit 1
fi
